import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from auditor import ask
from auditor.core import embedding_index, triage_agent
from auditor.core.embedding_index import ModeloNoDisponibleError
from auditor.core.models import AuditReport, worst_verdict
from auditor.core.orchestrator import add_result
from auditor.core.orchestrator import run as run_orchestrator
from auditor.core.repo_context import RepoContext
from auditor.report import to_json, to_markdown
from auditor.verifiers import build_check, deps_check, readme_check, secrets, semantic_check

_PASSIVE_VERIFIERS = {
    "secrets": secrets.verify,
    "readme_check": readme_check.verify,
    "deps_check": deps_check.verify,
}
_RUN_TESTS_WARNING = (
    "Este repo va a ejecutar código real (pytest) para build_check. ¿Confirmás? [y/N] "
)
# Solo https://github.com/<owner>/<repo> - nada de userinfo (credenciales embebidas),
# nada de esquemas alternativos (ext::, file://, ssh://) que git interpreta como
# transportes propios y pueden ejecutar comandos arbitrarios (ext::) o leer el
# filesystem local (file://). Un "-" inicial tambien queda afuera: pasado tal cual a
# git clone, una URL como "--upload-pack=..." se interpreta como flag, no como URL.
_GITHUB_URL = re.compile(r"^https://github\.com/[\w.-]+/[\w.-]+(?:\.git)?/?$")


class InvalidUrlError(ValueError):
    pass


def _validate_github_url(url: str) -> str:
    if not _GITHUB_URL.match(url):
        raise InvalidUrlError(
            f"URL invalida: '{url}'. Solo se aceptan URLs https://github.com/<owner>/<repo>"
        )
    return url


def _clone(url: str, dest: Path) -> None:
    subprocess.run(
        ["git", "clone", "--depth", "1", "--", url, str(dest)],
        check=True,
        capture_output=True,
        text=True,
    )


def _confirm_run_tests() -> bool:
    if not sys.stdin.isatty():
        return False
    try:
        answer = input(_RUN_TESTS_WARNING)
    except EOFError:
        return False
    return answer.strip().lower() == "y"


def _apply_triage(report: AuditReport, repo_path: Path) -> AuditReport:
    """El triage corre en su propio borde: si explota, el reporte sale igual
    con lo que ya verificaron los demas (ADR 0003, Mitigacion 4). Sin este
    try, una excepcion en el agente tiraria tambien los resultados de los
    verificadores deterministas que ya habian terminado bien."""
    try:
        results = triage_agent.triage(repo_path, report.verifier_results)
    except Exception:  # noqa: BLE001 - ninguna falla del agente puede tumbar el reporte
        return report

    return AuditReport(
        final_verdict=worst_verdict(r.verdict for r in results.values()),
        verifier_results=results,
        skipped_verifiers=report.skipped_verifiers,
    )


def _encoder_de_ask():
    """Aislada para poder sustituirla en los tests sin tocar red ni disco.

    Se resuelve el encoder ACA y no adentro de `ask.buscar` para que el fallo
    de carga del modelo se reporte antes de trocear el repo entero: sin
    modelo no hay respuesta posible, y hacer el trabajo para después no poder
    usarlo es gasto puro.
    """
    return embedding_index._encoder()


def _responder_pregunta(repo_path: Path, pregunta: str, como_json: bool) -> int:
    """`--ask` corta el flujo de auditoría: no corre verificadores, no arma
    reporte y no emite veredicto (ADR 0005). La pregunta es exploratoria."""
    try:
        encoder = _encoder_de_ask()
    except ModeloNoDisponibleError as exc:
        # Degradar en silencio a una lista vacía sería peor que fallar: se
        # leería como "no hay nada que ver acá".
        print(str(exc), file=sys.stderr)
        return 1

    respuesta = ask.buscar(repo_path, pregunta, encoder=encoder)
    print(ask.to_json(respuesta) if como_json else ask.to_texto(respuesta))
    return 0


def _use_utf8_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except AttributeError:
            pass


def main(argv: list[str] | None = None) -> int:
    _use_utf8_streams()

    parser = argparse.ArgumentParser(prog="auditor")
    parser.add_argument("url", help="URL del repo de GitHub a auditar")
    parser.add_argument(
        "--run-tests",
        action="store_true",
        help="corre build_check (ejecuta pytest real del repo auditado)",
    )
    parser.add_argument("--json", action="store_true", help="salida en JSON en vez de Markdown")
    parser.add_argument(
        "--semantic",
        action="store_true",
        help="corre semantic_check (usa la API de Groq, requiere GROQ_API_KEY)",
    )
    parser.add_argument(
        "--triage",
        action="store_true",
        help=(
            "revisa los hallazgos ambiguos de secrets con el agente de triage "
            "(usa la API de Groq, requiere GROQ_API_KEY)"
        ),
    )
    parser.add_argument(
        "--ask",
        metavar="PREGUNTA",
        help=(
            "busca en el código los fragmentos más relacionados con una pregunta "
            "y los lista con archivo:línea. Exploratorio: no emite veredicto ni "
            "corre los verificadores — te muestra dónde mirar, no te dice si algo "
            "es cierto"
        ),
    )
    args = parser.parse_args(argv)

    try:
        _validate_github_url(args.url)
    except InvalidUrlError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.ask:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_path = Path(tmp_dir) / "repo"
            try:
                _clone(args.url, repo_path)
            except subprocess.CalledProcessError as exc:
                print(f"No se pudo clonar {args.url}: {exc.stderr.strip()}", file=sys.stderr)
                return 1
            return _responder_pregunta(repo_path, args.ask, args.json)

    verifiers = dict(_PASSIVE_VERIFIERS)
    skipped: list[str] = []
    if args.run_tests or _confirm_run_tests():
        verifiers["build_check"] = build_check.verify
    else:
        skipped.append("build_check")

    with tempfile.TemporaryDirectory() as tmp_dir:
        repo_path = Path(tmp_dir) / "repo"
        try:
            _clone(args.url, repo_path)
        except subprocess.CalledProcessError as exc:
            print(f"No se pudo clonar {args.url}: {exc.stderr.strip()}", file=sys.stderr)
            return 1

        ctx = RepoContext.from_path(repo_path)
        report = run_orchestrator(ctx, verifiers, skipped_verifiers=skipped)

        if args.triage:
            report = _apply_triage(report, ctx.path)

        if args.semantic:
            semantic_result = semantic_check.verify(ctx, report.verifier_results)
            report = add_result(report, "semantic_check", semantic_result)

    output = to_json(report, args.url) if args.json else to_markdown(report, args.url)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

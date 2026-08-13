import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from auditor.core.orchestrator import run as run_orchestrator
from auditor.core.repo_context import RepoContext
from auditor.report import to_json, to_markdown
from auditor.verifiers import build_check, deps_check, readme_check, secrets

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
    args = parser.parse_args(argv)

    try:
        _validate_github_url(args.url)
    except InvalidUrlError as exc:
        print(str(exc), file=sys.stderr)
        return 1

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

    output = to_json(report, args.url) if args.json else to_markdown(report, args.url)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

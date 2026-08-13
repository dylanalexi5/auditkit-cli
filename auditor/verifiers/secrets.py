from pathlib import Path

from detect_secrets.core import baseline
from detect_secrets.settings import default_settings

from auditor.core.models import Evidence, Verdict, VerifierResult
from auditor.core.repo_context import RepoContext

# Artefactos generados: por las herramientas del repo o por el propio auditor
# al correr build_check. No son codigo del repo auditado, asi que no son parte
# de lo que se audita. Sin este filtro, build_check deja .pytest_cache/ en el
# clon y secrets marca su CACHEDIR.TAG como "Hex High Entropy String" - el
# veredicto pasaba a depender del orden en que corren los verificadores.
_GENERATED_DIRS = frozenset(
    {
        ".pytest_cache",
        "__pycache__",
        ".ruff_cache",
        ".mypy_cache",
        ".tox",
        ".nox",
        ".eggs",
        "htmlcov",
    }
)
_GENERATED_DIR_SUFFIXES = (".egg-info",)


def _is_generated_artifact(filename: str) -> bool:
    return any(
        part in _GENERATED_DIRS or part.endswith(_GENERATED_DIR_SUFFIXES)
        for part in Path(filename).parts
    )


def verify(ctx: RepoContext) -> VerifierResult:
    with default_settings():
        found = baseline.create(str(ctx.path), should_scan_all_files=True, root=str(ctx.path))

    evidence = [
        Evidence(file=filename, line=secret.line_number, note=secret.type)
        for filename, secrets_in_file in found.data.items()
        if not _is_generated_artifact(filename)
        for secret in secrets_in_file
    ]

    verdict = Verdict.NO_SOSTENIBLE if evidence else Verdict.APROBADO
    return VerifierResult(verdict=verdict, evidence=evidence)

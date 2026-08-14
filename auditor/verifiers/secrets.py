import json
import tempfile
from pathlib import Path

from detect_secrets.core import baseline
from detect_secrets.core.secrets_collection import SecretsCollection
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

# .pre-commit-config.yaml declara sus hooks con `rev: <sha git de 40 hex>`
# ("frozen" a un commit puntual) - son hashes de version, no secretos. Un repo
# real con hooks pineados (pallets/click, ej.) dispara "Hex High Entropy
# String" en cada rev. Se excluye por nombre de archivo, no por heuristica -
# ver ADR 0001, seccion de limitaciones.
_EXCLUDED_FILENAMES = frozenset({".pre-commit-config.yaml"})


def _is_excluded(filename: str) -> bool:
    parts = Path(filename).parts
    if any(part in _GENERATED_DIRS or part.endswith(_GENERATED_DIR_SUFFIXES) for part in parts):
        return True
    return Path(filename).name in _EXCLUDED_FILENAMES


def _notebook_cell_sources(notebook_path: Path) -> list[str]:
    """Fuente de cada celda de un .ipynb, en orden. Nunca la metadata:
    detect-secrets no tiene plugin para notebooks (confirmado - no hay nada
    ipynb/jupyter en su paquete), y la metadata de Jupyter esta llena de
    strings hex que parecen secretos sin serlo: hash del interprete
    (`metadata.interpreter.hash`), uuid de celda estilo Kaggle
    (`cell.metadata._uuid`), id de celda (nbformat 4.5+). Ninguno lo escribio
    un humano - son bookkeeping del propio Jupyter. Un secreto de verdad,
    pegado a mano en una celda de codigo, vive en `source` - eso si se
    escanea."""
    try:
        data = json.loads(notebook_path.read_text(encoding="utf-8", errors="ignore"))
    except json.JSONDecodeError:
        return []

    sources = []
    for cell in data.get("cells", []):
        source = cell.get("source", "")
        text = "".join(source) if isinstance(source, list) else str(source)
        sources.append(text)
    return sources


def _scan_notebooks(root: Path) -> list[Evidence]:
    evidence: list[Evidence] = []
    for notebook_path in root.rglob("*.ipynb"):
        relative = str(notebook_path.relative_to(root))
        if _is_excluded(relative):
            continue

        for cell_index, source in enumerate(_notebook_cell_sources(notebook_path), start=1):
            if not source.strip():
                continue

            with tempfile.NamedTemporaryFile(
                "w", suffix=".py", delete=False, encoding="utf-8"
            ) as tmp_file:
                tmp_file.write(source)
                tmp_path = Path(tmp_file.name)

            try:
                collection = SecretsCollection()
                with default_settings():
                    collection.scan_file(str(tmp_path))
                for secrets_in_file in collection.data.values():
                    for secret in secrets_in_file:
                        evidence.append(
                            Evidence(file=relative, line=cell_index, note=secret.type)
                        )
            finally:
                tmp_path.unlink(missing_ok=True)

    return evidence


def verify(ctx: RepoContext) -> VerifierResult:
    with default_settings():
        found = baseline.create(str(ctx.path), should_scan_all_files=True, root=str(ctx.path))

    evidence = [
        Evidence(file=filename, line=secret.line_number, note=secret.type)
        for filename, secrets_in_file in found.data.items()
        if not _is_excluded(filename) and not filename.endswith(".ipynb")
        for secret in secrets_in_file
    ]
    evidence.extend(_scan_notebooks(ctx.path))

    verdict = Verdict.NO_SOSTENIBLE if evidence else Verdict.APROBADO
    return VerifierResult(verdict=verdict, evidence=evidence)

import ast
import json
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

from auditor.core.models import Evidence, Verdict, VerifierResult, worst_verdict
from auditor.core.repo_context import RepoContext, normalize_dependency_name, read_pyproject_toml

_TIMEOUT_SECONDS = 120
_VULN_SCAN_TIMEOUT_NOTE = (
    "pip-audit no pudo completarse a tiempo - vulnerabilidades no verificadas"
)
_NAME_TOKEN = re.compile(r"^[A-Za-z0-9_.\-]+")
_VCS_PREFIXES = ("git+", "hg+", "bzr+", "svn+")

# Marcador explicito para cuando no hay ningun archivo de dependencias que citar.
# Inventar "requirements.txt:1" en un repo que no tiene requirements.txt destruye
# la premisa de la herramienta: la evidencia tiene que ser verificable abriendo
# el archivo citado.
_NO_DEPS_FILE = "(no se encontró archivo de dependencias)"
_DEPS_FILES = ("requirements.txt", "pyproject.toml", "poetry.lock")

# Mapeo import -> paquete PyPI para los casos mas comunes donde divergen.
# Lista abierta, no exhaustiva - se amplia cuando aparezca un caso real.
_KNOWN_IMPORT_TO_PACKAGE = {
    "sklearn": "scikit-learn",
    "bs4": "beautifulsoup4",
    "pil": "pillow",
    "yaml": "pyyaml",
    "cv2": "opencv-python",
}


def _is_safe_requirement_line(line: str) -> bool:
    """Rechaza VCS/URL/local-path requirements - pip-audit los resolveria via pip,
    lo que puede descargar y ejecutar el build backend (setup.py/pyproject.toml)
    de una fuente que el repo auditado controla por completo."""
    lowered = line.lower()
    if "://" in lowered or lowered.startswith(_VCS_PREFIXES):
        return False
    return bool(_NAME_TOKEN.match(line))


def _parse_poetry_lock(path: Path) -> list[tuple[str, int, str]]:
    """poetry.lock trae versiones exactas ya resueltas - mas confiable que
    aproximar los rangos ^/~ de [tool.poetry.dependencies] a mano."""
    lock_file = path / "poetry.lock"
    if not lock_file.is_file():
        return []

    try:
        data = tomllib.loads(lock_file.read_text(encoding="utf-8", errors="ignore"))
    except tomllib.TOMLDecodeError:
        return []

    entries = []
    for pkg in data.get("package", []):
        name = str(pkg.get("name", "")).strip()
        version = str(pkg.get("version", "")).strip()
        if not name or not version:
            continue
        requirement = f"{name}=={version}"
        if _is_safe_requirement_line(requirement):
            entries.append(("poetry.lock", 1, requirement))
    return entries


def _extract_requirements(path: Path) -> list[tuple[str, int, str]]:
    entries: list[tuple[str, int, str]] = []
    req_file = path / "requirements.txt"
    if req_file.is_file():
        lines = req_file.read_text(encoding="utf-8", errors="ignore").splitlines()
        for line_number, line in enumerate(lines, start=1):
            stripped = line.strip()
            if stripped and not stripped.startswith(("#", "-")) and _is_safe_requirement_line(
                stripped
            ):
                entries.append(("requirements.txt", line_number, stripped))

    entries.extend(_parse_poetry_lock(path))

    for dep in read_pyproject_toml(path).get("project", {}).get("dependencies", []):
        if _is_safe_requirement_line(dep.strip()):
            entries.append(("pyproject.toml", 1, dep))

    return entries


def _deps_file_fallback(path: Path) -> tuple[str, int]:
    """Cuando no se puede ubicar la linea exacta, citar un archivo de dependencias
    que exista de verdad. Si el repo no tiene ninguno, decirlo explicitamente en
    vez de inventar una ruta."""
    for name in _DEPS_FILES:
        if (path / name).is_file():
            return name, 1
    return _NO_DEPS_FILE, 0


def _locate(
    entries: list[tuple[str, int, str]], normalized_name: str, path: Path
) -> tuple[str, int]:
    for source_file, line_number, raw in entries:
        match = _NAME_TOKEN.match(raw.strip())
        if match and normalize_dependency_name(match.group(0)) == normalized_name:
            return source_file, line_number
    return _deps_file_fallback(path)


def _run_pip_audit(entries: list[tuple[str, int, str]]) -> list[dict] | None:
    """Devuelve None si pip-audit no pudo correr (timeout u otra falla del
    subprocess) - distinto de [] (corrio bien, nada para reportar). Confundir
    "no se pudo verificar" con "verificado, esta limpio" seria mentir."""
    if not entries:
        return []

    with tempfile.NamedTemporaryFile(
        "w", suffix=".txt", delete=False, encoding="utf-8"
    ) as tmp_file:
        tmp_file.write("\n".join(raw for _, _, raw in entries) + "\n")
        req_path = Path(tmp_file.name)

    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip_audit",
                "-r",
                str(req_path),
                "-f",
                "json",
                "--progress-spinner",
                "off",
            ],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    finally:
        req_path.unlink(missing_ok=True)

    try:
        return json.loads(result.stdout).get("dependencies", [])
    except json.JSONDecodeError:
        return None


def _top_level_imports(path: Path) -> set[str]:
    names: set[str] = set()
    for py_file in path.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names.add(node.module.split(".")[0])
    return {normalize_dependency_name(name) for name in names}


def _local_top_level_names(path: Path) -> set[str]:
    names: set[str] = set()
    for child in path.iterdir():
        if child.is_dir() and (child / "__init__.py").is_file():
            names.add(normalize_dependency_name(child.name))
        elif child.is_file() and child.suffix == ".py":
            names.add(normalize_dependency_name(child.stem))
    return names


def verify(ctx: RepoContext) -> VerifierResult:
    evidence: list[Evidence] = []
    verdict = Verdict.APROBADO

    def escalate(new_verdict: Verdict) -> None:
        nonlocal verdict
        verdict = worst_verdict([verdict, new_verdict])

    entries = _extract_requirements(ctx.path)
    pip_audit_results = _run_pip_audit(entries)
    if pip_audit_results is None:
        evidence.append(Evidence(file="pip-audit", line=0, note=_VULN_SCAN_TIMEOUT_NOTE))
        escalate(Verdict.APROBADO_CON_OBSERVACIONES)
        pip_audit_results = []

    for dep in pip_audit_results:
        vulns = dep.get("vulns") or []
        if not vulns:
            continue
        normalized_name = normalize_dependency_name(dep.get("name", ""))
        source_file, line_number = _locate(entries, normalized_name, ctx.path)
        vuln_ids = ", ".join(sorted({v["id"] for v in vulns}))
        evidence.append(
            Evidence(
                file=source_file,
                line=line_number,
                note=(
                    f"{dep.get('name')} {dep.get('version')} tiene vulnerabilidades "
                    f"conocidas: {vuln_ids}"
                ),
            )
        )
        escalate(Verdict.NO_SOSTENIBLE)

    undeclared_file, undeclared_line = _deps_file_fallback(ctx.path)
    used_imports = _top_level_imports(ctx.path)
    local_names = _local_top_level_names(ctx.path)
    stdlib_names = {normalize_dependency_name(name) for name in sys.stdlib_module_names}

    for module_name in sorted(used_imports - ctx.declared_dependencies):
        if module_name in stdlib_names or module_name in local_names:
            continue

        mapped_package = _KNOWN_IMPORT_TO_PACKAGE.get(module_name)
        mapped_normalized = (
            normalize_dependency_name(mapped_package) if mapped_package else None
        )
        if mapped_normalized and mapped_normalized in ctx.declared_dependencies:
            source_file, line_number = _locate(entries, mapped_normalized, ctx.path)
            evidence.append(
                Evidence(
                    file=source_file,
                    line=line_number,
                    note=(
                        f"'{module_name}' se importa pero fue declarado como "
                        f"'{mapped_package}' - mapeo conocido, no es un fallo real"
                    ),
                )
            )
            escalate(Verdict.APROBADO_CON_OBSERVACIONES)
        else:
            evidence.append(
                Evidence(
                    file=undeclared_file,
                    line=undeclared_line,
                    note=(
                        f"'{module_name}' se importa en el codigo pero no esta declarado "
                        "en requirements.txt/pyproject.toml"
                    ),
                )
            )
            escalate(Verdict.NO_SOSTENIBLE)

    effectively_used = used_imports | {
        normalize_dependency_name(pkg)
        for imp, pkg in _KNOWN_IMPORT_TO_PACKAGE.items()
        if imp in used_imports
    }
    for normalized_name in sorted(ctx.declared_dependencies - effectively_used):
        source_file, line_number = _locate(entries, normalized_name, ctx.path)
        evidence.append(
            Evidence(
                file=source_file,
                line=line_number,
                note=f"'{normalized_name}' esta declarado pero no se usa en el codigo",
            )
        )
        escalate(Verdict.APROBADO_CON_OBSERVACIONES)

    return VerifierResult(verdict=verdict, evidence=evidence)

import ast
import json
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

from auditor.core.models import Evidence, Verdict, VerifierResult, worst_verdict
from auditor.core.repo_context import (
    RepoContext,
    declared_project_names,
    is_test_fixture_path,
    normalize_dependency_name,
    read_pyproject_toml,
)

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
_VIA_COMMENT = re.compile(r"#\s*via\b", re.IGNORECASE)

# Herramientas que viven por fuera del proyecto: se instalan aparte y se invocan
# como comando. Se consulta en las dos direcciones, porque aparecen de las dos
# formas segun la herramienta:
#   - declaradas sin importarse (ruff, coverage): buscar su `import` y no
#     encontrarlo no prueba nada.
#   - importadas sin declararse (nox, tox): un noxfile.py necesita nox instalado
#     ANTES que el proyecto, asi que no es una dependencia que el repo declare.
# Las dos dan APROBADO_CON_OBSERVACIONES, nunca NO_SOSTENIBLE. Ver ADR.
# Lista abierta, igual que el mapeo import->paquete de abajo.
_CLI_ONLY_PACKAGES = {
    "ruff",
    "coverage",
    "nox",
    "tox",
    "check_manifest",
    "pip_audit",
    "pytest",
    "pytest_cov",
    "pytest_asyncio",
    "black",
    "mypy",
    "flake8",
    "isort",
    "pylint",
    "bandit",
    "pre_commit",
    "twine",
    "build",
    "setuptools",
    "wheel",
    "pip",
    "sphinx",
    "mkdocs",
    "detect_secrets",
}

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


def _is_type_checking_guard(test: ast.expr) -> bool:
    """`if TYPE_CHECKING:` / `if typing.TYPE_CHECKING:` / `if t.TYPE_CHECKING:`
    (cualquier alias) - el body nunca corre en runtime, un type-checker lo lee
    y nada mas. pallets/click hace exactamente esto con typing_extensions:
    `if t.TYPE_CHECKING: import typing_extensions` en varios modulos - nunca
    es una dependencia de runtime, pero un ast.walk() ciego a la estructura
    no distingue esto de un import real."""
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


class _RuntimeImportVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_If(self, node: ast.If) -> None:
        if _is_type_checking_guard(node.test):
            # El body es solo para type-checkers - no se visita. El else (si
            # existe, ej. un fallback en runtime) si es codigo real.
            for stmt in node.orelse:
                self.visit(stmt)
            return
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        self.names.update(alias.name.split(".")[0] for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module and node.level == 0:
            self.names.add(node.module.split(".")[0])


def _top_level_imports(path: Path) -> set[str]:
    names: set[str] = set()
    for py_file in path.rglob("*.py"):
        relative_parts = py_file.relative_to(path).parts
        if is_test_fixture_path(relative_parts):
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        visitor = _RuntimeImportVisitor()
        visitor.visit(tree)
        names.update(visitor.names)
    return {normalize_dependency_name(name) for name in names}


def _scan_dir_for_modules(directory: Path) -> set[str]:
    names: set[str] = set()
    if not directory.is_dir():
        return names
    for child in directory.iterdir():
        if child.is_dir() and (child / "__init__.py").is_file():
            names.add(normalize_dependency_name(child.name))
        elif child.is_file() and child.suffix == ".py":
            names.add(normalize_dependency_name(child.stem))
    return names


def _local_top_level_names(path: Path) -> set[str]:
    """Incluye el layout src/: con `src/<paquete>/` el paquete propio del repo no
    es visible iterando solo la raiz, y termina reportado como import no
    declarado - un NO_SOSTENIBLE falso contra el propio codigo del repo."""
    return (
        _scan_dir_for_modules(path)
        | _scan_dir_for_modules(path / "src")
        | set(declared_project_names(path))
    )


def _transitive_pins(path: Path) -> set[str]:
    """Un comentario `# via X` es la marca que dejan pip-compile y los
    requirements.txt anotados para las dependencias transitivas. No son deps
    directas del repo, asi que "declarada pero no se usa" para ellas es ruido:
    el archivo mismo ya dice quien las arrastra."""
    req_file = path / "requirements.txt"
    if not req_file.is_file():
        return set()

    names: set[str] = set()
    for line in req_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or not _VIA_COMMENT.search(stripped):
            continue
        match = _NAME_TOKEN.match(stripped)
        if match:
            names.add(normalize_dependency_name(match.group(0)))
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
        elif module_name in _CLI_ONLY_PACKAGES:
            evidence.append(
                Evidence(
                    file=undeclared_file,
                    line=undeclared_line,
                    note=(
                        f"'{module_name}' se importa sin declarar, pero es una herramienta "
                        "de build/automatizacion: tiene que estar instalada por fuera del "
                        "proyecto para poder correr el script que la invoca"
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
    no_reportables = _transitive_pins(ctx.path) | _CLI_ONLY_PACKAGES
    for normalized_name in sorted(ctx.declared_dependencies - effectively_used):
        if normalized_name in no_reportables:
            continue
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

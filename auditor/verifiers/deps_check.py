import ast
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from auditor.core.models import Evidence, Verdict, VerifierResult, worst_verdict
from auditor.core.repo_context import RepoContext, normalize_dependency_name, read_pyproject_toml

_TIMEOUT_SECONDS = 60
_NAME_TOKEN = re.compile(r"^[A-Za-z0-9_.\-]+")

# Mapeo import -> paquete PyPI para los casos mas comunes donde divergen.
# Lista abierta, no exhaustiva - se amplia cuando aparezca un caso real.
_KNOWN_IMPORT_TO_PACKAGE = {
    "sklearn": "scikit-learn",
    "bs4": "beautifulsoup4",
    "pil": "pillow",
    "yaml": "pyyaml",
    "cv2": "opencv-python",
}


def _extract_requirements(path: Path) -> list[tuple[str, int, str]]:
    entries: list[tuple[str, int, str]] = []
    req_file = path / "requirements.txt"
    if req_file.is_file():
        lines = req_file.read_text(encoding="utf-8", errors="ignore").splitlines()
        for line_number, line in enumerate(lines, start=1):
            stripped = line.strip()
            if stripped and not stripped.startswith(("#", "-")):
                entries.append(("requirements.txt", line_number, stripped))

    for dep in read_pyproject_toml(path).get("project", {}).get("dependencies", []):
        entries.append(("pyproject.toml", 1, dep))

    return entries


def _locate(entries: list[tuple[str, int, str]], normalized_name: str) -> tuple[str, int]:
    for source_file, line_number, raw in entries:
        match = _NAME_TOKEN.match(raw.strip())
        if match and normalize_dependency_name(match.group(0)) == normalized_name:
            return source_file, line_number
    return "requirements.txt", 1


def _run_pip_audit(entries: list[tuple[str, int, str]]) -> list[dict]:
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
    finally:
        req_path.unlink(missing_ok=True)

    try:
        return json.loads(result.stdout).get("dependencies", [])
    except json.JSONDecodeError:
        return []


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
    for dep in _run_pip_audit(entries):
        vulns = dep.get("vulns") or []
        if not vulns:
            continue
        normalized_name = normalize_dependency_name(dep.get("name", ""))
        source_file, line_number = _locate(entries, normalized_name)
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
            source_file, line_number = _locate(entries, mapped_normalized)
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
                    file="requirements.txt" if entries else "pyproject.toml",
                    line=1,
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
        source_file, line_number = _locate(entries, normalized_name)
        evidence.append(
            Evidence(
                file=source_file,
                line=line_number,
                note=f"'{normalized_name}' esta declarado pero no se usa en el codigo",
            )
        )
        escalate(Verdict.APROBADO_CON_OBSERVACIONES)

    return VerifierResult(verdict=verdict, evidence=evidence)

import itertools
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

_NAME_TOKEN = re.compile(r"^[A-Za-z0-9_.\-]+")

_TEST_FIXTURE_DIR_NAMES = frozenset({"tests", "test"})
_TEST_FIXTURE_SUBDIR_NAMES = frozenset({"data", "fixtures"})


def is_test_fixture_path(relative_parts: tuple[str, ...]) -> bool:
    """tests/data/ y tests/fixtures/ (a cualquier profundidad debajo) son la
    convencion del ecosistema para guardar codigo de ejemplo usado COMO DATO
    de un test, no codigo real del proyecto. psf/black tiene una carpeta
    entera, tests/data/cases/, con archivos .py que son literalmente input
    de prueba para el formateador - contienen `import foo`, `import hello`,
    nombres inventados a proposito, y el ast scan los leia como si fueran
    dependencias reales del propio black.

    Vive aca y no en `deps_check.py` por la misma razon que
    `declared_project_names`: lo comparten dos consumidores (`deps_check.py`
    y `symbol_index.py`) y `core/` no puede depender de `verifiers/`."""
    lowered = [part.lower() for part in relative_parts]
    return any(
        a in _TEST_FIXTURE_DIR_NAMES and b in _TEST_FIXTURE_SUBDIR_NAMES
        for a, b in itertools.pairwise(lowered)
    )


def normalize_dependency_name(name: str) -> str:
    return name.split(".")[0].strip().lower().replace("-", "_")


def _parse_requirements_txt(path: Path) -> set[str]:
    req_file = path / "requirements.txt"
    if not req_file.is_file():
        return set()

    names = set()
    for line in req_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-")):
            continue
        match = _NAME_TOKEN.match(line)
        if match:
            names.add(normalize_dependency_name(match.group(0)))
    return names


def read_pyproject_toml(path: Path) -> dict:
    pyproject_file = path / "pyproject.toml"
    if not pyproject_file.is_file():
        return {}

    try:
        return tomllib.loads(pyproject_file.read_text(encoding="utf-8", errors="ignore"))
    except tomllib.TOMLDecodeError:
        return {}


def _parse_pyproject_toml(path: Path) -> set[str]:
    data = read_pyproject_toml(path)
    names = set()

    # PEP 621 - lista de strings "name>=version"
    project = data.get("project", {})
    for dep in project.get("dependencies", []):
        match = _NAME_TOKEN.match(dep.strip())
        if match:
            names.add(normalize_dependency_name(match.group(0)))
    for group_deps in project.get("optional-dependencies", {}).values():
        for dep in group_deps:
            match = _NAME_TOKEN.match(dep.strip())
            if match:
                names.add(normalize_dependency_name(match.group(0)))

    # PEP 735 - [dependency-groups], tabla top-level (no bajo [project]). Cada
    # entrada de una lista es un string "name>=version" o un dict
    # {"include-group": "otro-grupo"} (referencia a otro grupo, no un
    # paquete) - se ignoran esos, no tienen nombre de paquete que extraer.
    # pallets/click declara asi sus deps de docs (pallets-sphinx-themes,
    # sphinx...) en vez de [project.optional-dependencies].
    for group_deps in data.get("dependency-groups", {}).values():
        for dep in group_deps:
            if not isinstance(dep, str):
                continue
            match = _NAME_TOKEN.match(dep.strip())
            if match:
                names.add(normalize_dependency_name(match.group(0)))

    # Poetry - tabla { name = "version" | {version=..., extras=...} }, no lista de strings
    poetry = data.get("tool", {}).get("poetry", {})
    for name in poetry.get("dependencies", {}):
        if name.lower() != "python":
            names.add(normalize_dependency_name(name))
    for group in poetry.get("group", {}).values():
        for name in group.get("dependencies", {}):
            names.add(normalize_dependency_name(name))

    return names


def parse_declared_dependencies(path: Path) -> frozenset[str]:
    return frozenset(_parse_requirements_txt(path) | _parse_pyproject_toml(path))


def declared_project_names(path: Path) -> frozenset[str]:
    """El/los nombre(s) que el propio repo declara para si mismo (PEP 621 o
    Poetry). Compartido entre deps_check.py (no reportar el paquete propio
    como import no declarado) y semantic_check.py (no cruzar por el nombre
    del proyecto, que aparece en casi cualquier afirmacion sobre si mismo -
    ver ADR 0002)."""
    data = read_pyproject_toml(path)
    candidates = (
        data.get("project", {}).get("name"),
        data.get("tool", {}).get("poetry", {}).get("name"),
    )
    return frozenset(normalize_dependency_name(str(name)) for name in candidates if name)


@dataclass(frozen=True)
class RepoContext:
    path: Path
    declared_dependencies: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_path(cls, path: Path) -> "RepoContext":
        return cls(path=path, declared_dependencies=parse_declared_dependencies(path))

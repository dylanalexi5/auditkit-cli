import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

_NAME_TOKEN = re.compile(r"^[A-Za-z0-9_.\-]+")


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


def _parse_pyproject_toml(path: Path) -> set[str]:
    pyproject_file = path / "pyproject.toml"
    if not pyproject_file.is_file():
        return set()

    try:
        data = tomllib.loads(pyproject_file.read_text(encoding="utf-8", errors="ignore"))
    except tomllib.TOMLDecodeError:
        return set()

    names = set()
    for dep in data.get("project", {}).get("dependencies", []):
        match = _NAME_TOKEN.match(dep.strip())
        if match:
            names.add(normalize_dependency_name(match.group(0)))
    return names


def parse_declared_dependencies(path: Path) -> frozenset[str]:
    return frozenset(_parse_requirements_txt(path) | _parse_pyproject_toml(path))


@dataclass(frozen=True)
class RepoContext:
    path: Path
    declared_dependencies: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_path(cls, path: Path) -> "RepoContext":
        return cls(path=path, declared_dependencies=parse_declared_dependencies(path))

from pathlib import Path

from auditor.core.repo_context import RepoContext, declarado_como


def test_from_path_parses_requirements_txt(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text(
        "Some-Package==1.2.3\n# comment\nclick>=8.0\n"
    )

    ctx = RepoContext.from_path(tmp_path)

    assert ctx.declared_dependencies == {"some_package", "click"}


def test_from_path_parses_pyproject_dependencies(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\ndependencies = ["Requests>=2.0", "pyyaml"]\n'
    )

    ctx = RepoContext.from_path(tmp_path)

    assert ctx.declared_dependencies == {"requests", "pyyaml"}


def test_from_path_parses_poetry_dependencies(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.poetry.dependencies]\n"
        'python = "^3.11"\n'
        'fastapi = "^0.115.0"\n'
        'pydantic-settings = "^2.7.0"\n'
        "[tool.poetry.group.dev.dependencies]\n"
        'pytest = "^8.3.0"\n'
    )

    ctx = RepoContext.from_path(tmp_path)

    assert ctx.declared_dependencies == {"fastapi", "pydantic_settings", "pytest"}


def test_from_path_parses_dependency_groups(tmp_path: Path) -> None:
    """PEP 735 [dependency-groups] - pallets/click declara asi sus deps de
    docs (pallets-sphinx-themes, sphinx...) en vez de
    [project.optional-dependencies]. Una entrada {"include-group": "..."}
    referencia otro grupo, no un paquete - se ignora, no tiene nombre real."""
    (tmp_path / "pyproject.toml").write_text(
        "[dependency-groups]\n"
        'dev = ["ruff", "tox"]\n'
        'docs = ["myst-parser", "pallets-sphinx-themes", {include-group = "dev"}]\n'
    )

    ctx = RepoContext.from_path(tmp_path)

    assert ctx.declared_dependencies == {"ruff", "tox", "myst_parser", "pallets_sphinx_themes"}


def test_from_path_survives_malformed_pyproject_toml(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("this is not [ valid toml")
    (tmp_path / "requirements.txt").write_text("click>=8.0\n")

    ctx = RepoContext.from_path(tmp_path)

    assert ctx.declared_dependencies == {"click"}


def test_declarado_como_devuelve_el_paquete_cuando_esta_declarado() -> None:
    """`arrow-py/arrow` declara `python-dateutil` e importa `dateutil`."""
    assert declarado_como("dateutil", frozenset({"python_dateutil"})) == "python-dateutil"


def test_declarado_como_es_none_si_el_paquete_mapeado_no_esta_declarado() -> None:
    """El mapeo existe pero nadie declaro el paquete: sigue siendo un import
    sin declarar, no un mapeo que lo salve."""
    assert declarado_como("dateutil", frozenset({"otra_cosa"})) is None


def test_declarado_como_es_none_sin_mapeo_conocido() -> None:
    assert declarado_como("modulo_sin_mapeo", frozenset({"modulo_sin_mapeo"})) is None

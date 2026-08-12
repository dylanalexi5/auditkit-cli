from pathlib import Path

from auditor.core.repo_context import RepoContext


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

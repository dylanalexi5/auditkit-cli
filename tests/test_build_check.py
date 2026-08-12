from pathlib import Path

from auditor.core.models import Verdict
from auditor.core.repo_context import RepoContext
from auditor.verifiers import build_check


def _write_pyproject(path: Path) -> None:
    (path / "pyproject.toml").write_text('[project]\nname = "fixture"\nversion = "0.0.1"\n')


def test_verify_fails_on_broken_import(tmp_path: Path) -> None:
    _write_pyproject(tmp_path)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_broken.py").write_text(
        "import nonexistent_module_xyz\n\n\ndef test_thing():\n    assert True\n"
    )

    result = build_check.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.NO_SOSTENIBLE
    assert any(
        "test_broken.py" in e.file and "nonexistent_module_xyz" in e.note
        for e in result.evidence
    )


def test_verify_module_not_found_with_declared_dependency_is_observaciones(
    tmp_path: Path,
) -> None:
    _write_pyproject(tmp_path)
    (tmp_path / "requirements.txt").write_text("nonexistent_module_xyz==1.0.0\n")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_broken.py").write_text(
        "import nonexistent_module_xyz\n\n\ndef test_thing():\n    assert True\n"
    )

    result = build_check.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.APROBADO_CON_OBSERVACIONES
    assert any(
        "test_broken.py" in e.file and "dependencia declarada no instalada" in e.note
        for e in result.evidence
    )


def test_verify_passes_on_working_tests(tmp_path: Path) -> None:
    _write_pyproject(tmp_path)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_ok.py").write_text("def test_thing():\n    assert True\n")

    result = build_check.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.APROBADO
    assert result.evidence == []


def test_verify_non_python_repo_is_aprobado(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# not a python project\n")

    result = build_check.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.APROBADO
    assert result.evidence == []

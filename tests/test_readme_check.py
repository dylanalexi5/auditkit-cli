from pathlib import Path

from auditor.core.models import Verdict
from auditor.core.repo_context import RepoContext
from auditor.verifiers import readme_check


def test_verify_flags_false_coverage_claim(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(
        "# demo\n\nThis project has 100% test coverage.\n"
    )

    result = readme_check.verify(RepoContext(path=tmp_path))

    assert result.verdict == Verdict.NO_SOSTENIBLE
    assert any(
        e.file == "README.md" and e.line == 3 and "coverage" in e.note.lower()
        for e in result.evidence
    )


def test_verify_true_coverage_claim_is_aprobado(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(
        "# demo\n\nThis project has 100% test coverage.\n"
    )
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_demo.py").write_text(
        "def test_something():\n    assert True\n"
    )

    result = readme_check.verify(RepoContext(path=tmp_path))

    assert result.verdict == Verdict.APROBADO
    assert result.evidence == []


def test_verify_no_coverage_claim_is_aprobado(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# demo\n\nJust a plain project.\n")

    result = readme_check.verify(RepoContext(path=tmp_path))

    assert result.verdict == Verdict.APROBADO
    assert result.evidence == []

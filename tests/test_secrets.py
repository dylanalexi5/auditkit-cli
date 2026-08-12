from pathlib import Path

from auditor.core.models import Verdict
from auditor.core.repo_context import RepoContext
from auditor.verifiers import secrets


def test_verify_detects_known_secret(tmp_path: Path) -> None:
    (tmp_path / "config.py").write_text('AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n')

    result = secrets.verify(RepoContext(path=tmp_path))

    assert result.verdict == Verdict.NO_SOSTENIBLE
    assert any(
        e.file == "config.py" and e.line == 1 and "AWS" in e.note
        for e in result.evidence
    )


def test_verify_clean_repo_is_aprobado(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def main() -> None:\n    pass\n")

    result = secrets.verify(RepoContext(path=tmp_path))

    assert result.verdict == Verdict.APROBADO
    assert result.evidence == []

from pathlib import Path

from auditor.core.models import Verdict
from auditor.core.repo_context import RepoContext
from auditor.verifiers import deps_check


def test_verify_used_import_with_known_mapping_is_observaciones(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("scikit-learn==1.5.0\n")
    (tmp_path / "app.py").write_text("import sklearn\n\nsklearn.__version__\n")

    result = deps_check.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.APROBADO_CON_OBSERVACIONES
    assert any(
        "sklearn" in e.note and "scikit-learn" in e.note and "mapeo conocido" in e.note
        for e in result.evidence
    )


def test_verify_real_known_vulnerability_is_no_sostenible(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("pyyaml==5.3\n")
    (tmp_path / "app.py").write_text("import yaml\n\nyaml.safe_load('a: 1')\n")

    result = deps_check.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.NO_SOSTENIBLE
    assert any("pyyaml" in e.note.lower() and "vulnerabilidad" in e.note.lower() for e in result.evidence)


def test_verify_declared_but_unused_is_observaciones(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("click>=8.0\n")
    (tmp_path / "app.py").write_text("print('hello')\n")

    result = deps_check.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.APROBADO_CON_OBSERVACIONES
    assert any(
        "click" in e.note and "declarado" in e.note and "no se usa" in e.note
        for e in result.evidence
    )


def test_verify_used_import_without_declaration_or_mapping_is_no_sostenible(
    tmp_path: Path,
) -> None:
    (tmp_path / "requirements.txt").write_text("click>=8.0\n")
    (tmp_path / "app.py").write_text("import totally_undeclared_thing\n")

    result = deps_check.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.NO_SOSTENIBLE
    assert any("totally_undeclared_thing" in e.note for e in result.evidence)


def test_verify_clean_repo_is_aprobado(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("click>=8.0\n")
    (tmp_path / "app.py").write_text("import click\n\nclick.echo('hi')\n")

    result = deps_check.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.APROBADO
    assert result.evidence == []

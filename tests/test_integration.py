from pathlib import Path

from auditor.core.models import Verdict
from auditor.core.orchestrator import run
from auditor.core.repo_context import RepoContext
from auditor.report import to_markdown
from auditor.verifiers import deps_check, readme_check, secrets

_PASSIVE_VERIFIERS = {
    "secrets": secrets.verify,
    "readme_check": readme_check.verify,
    "deps_check": deps_check.verify,
}


def test_report_surfaces_findings_from_multiple_verifiers(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# demo\n\nThis project has 100% test coverage.\n")
    (tmp_path / "requirements.txt").write_text("click>=8.0\n")
    (tmp_path / "app.py").write_text(
        'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n\nimport totally_undeclared_thing\n'
    )

    ctx = RepoContext.from_path(tmp_path)
    report = run(ctx, _PASSIVE_VERIFIERS)
    markdown = to_markdown(report, "https://example.com/demo.git")

    assert report.verifier_results["secrets"].verdict == Verdict.NO_SOSTENIBLE
    assert report.verifier_results["readme_check"].verdict == Verdict.NO_SOSTENIBLE
    assert report.verifier_results["deps_check"].verdict == Verdict.NO_SOSTENIBLE
    assert report.final_verdict == Verdict.NO_SOSTENIBLE

    assert "AWS" in markdown
    assert "coverage" in markdown.lower()
    assert "totally_undeclared_thing" in markdown


def test_secrets_verdict_does_not_depend_on_verifier_order(tmp_path: Path) -> None:
    """build_check corre pytest dentro del repo clonado y deja .pytest_cache/.
    Si secrets escanea despues, marcaba CACHEDIR.TAG como secreto: el mismo
    repo daba veredicto distinto segun el orden del dict de verificadores."""
    from auditor.verifiers import build_check

    def _fixture(root: Path) -> Path:
        root.mkdir()
        (root / "pyproject.toml").write_text(
            '[project]\nname = "fixture"\nversion = "0.0.1"\ndependencies = []\n'
        )
        tests_dir = root / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_ok.py").write_text("def test_thing():\n    assert True\n")
        return root

    primero_secrets = {"secrets": secrets.verify, "build_check": build_check.verify}
    primero_build = {"build_check": build_check.verify, "secrets": secrets.verify}

    veredictos = []
    for nombre, verifiers in (("a", primero_secrets), ("b", primero_build)):
        ctx = RepoContext.from_path(_fixture(tmp_path / nombre))
        report = run(ctx, verifiers)
        veredictos.append(report.verifier_results["secrets"])

    assert veredictos[0].verdict == veredictos[1].verdict
    assert veredictos[0].evidence == veredictos[1].evidence == []

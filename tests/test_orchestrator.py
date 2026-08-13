from pathlib import Path

from auditor.core.models import AuditReport, Evidence, Verdict, VerifierResult
from auditor.core.orchestrator import add_result, run
from auditor.core.repo_context import RepoContext


def test_add_result_merges_and_recomputes_worst_verdict(tmp_path: Path) -> None:
    ctx = RepoContext.from_path(tmp_path)
    report = run(ctx, {"secrets": lambda _ctx: VerifierResult(Verdict.APROBADO, [])})
    assert report.final_verdict == Verdict.APROBADO

    updated = add_result(
        report,
        "semantic_check",
        VerifierResult(
            Verdict.NO_SOSTENIBLE, [Evidence(file="README.md", line=1, note="algo")]
        ),
    )

    assert updated.final_verdict == Verdict.NO_SOSTENIBLE
    assert set(updated.verifier_results) == {"secrets", "semantic_check"}
    assert report.final_verdict == Verdict.APROBADO, "no debe mutar el reporte original"


def test_add_result_does_not_touch_skipped_verifiers() -> None:
    report = AuditReport(
        final_verdict=Verdict.APROBADO,
        verifier_results={},
        skipped_verifiers=["build_check"],
    )

    updated = add_result(report, "secrets", VerifierResult(Verdict.APROBADO, []))

    assert updated.skipped_verifiers == ["build_check"]

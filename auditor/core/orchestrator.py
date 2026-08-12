from collections.abc import Callable

from auditor.core.models import AuditReport, VerifierResult, worst_verdict
from auditor.core.repo_context import RepoContext

Verifier = Callable[[RepoContext], VerifierResult]


def run(
    ctx: RepoContext,
    verifiers: dict[str, Verifier],
    skipped_verifiers: list[str] | None = None,
) -> AuditReport:
    results = {name: verify(ctx) for name, verify in verifiers.items()}
    final_verdict = worst_verdict(result.verdict for result in results.values())
    return AuditReport(
        final_verdict=final_verdict,
        verifier_results=results,
        skipped_verifiers=list(skipped_verifiers or []),
    )

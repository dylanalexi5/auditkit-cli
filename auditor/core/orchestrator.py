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


def add_result(report: AuditReport, name: str, result: VerifierResult) -> AuditReport:
    """Funde un VerifierResult adicional en un AuditReport ya armado.

    Existe para verificadores como semantic_check.py que no cumplen el tipo
    Verifier uniforme - necesitan ver los VerifierResult de los demas, asi
    que corren aparte, despues de run(), en vez de vivir en el dict
    verifiers de arriba (ver ADR 0002)."""
    results = {**report.verifier_results, name: result}
    return AuditReport(
        final_verdict=worst_verdict(r.verdict for r in results.values()),
        verifier_results=results,
        skipped_verifiers=report.skipped_verifiers,
    )

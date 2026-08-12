from detect_secrets.core import baseline
from detect_secrets.settings import default_settings

from auditor.core.models import Evidence, Verdict, VerifierResult
from auditor.core.repo_context import RepoContext


def verify(ctx: RepoContext) -> VerifierResult:
    with default_settings():
        found = baseline.create(str(ctx.path), should_scan_all_files=True, root=str(ctx.path))

    evidence = [
        Evidence(file=filename, line=secret.line_number, note=secret.type)
        for filename, secrets_in_file in found.data.items()
        for secret in secrets_in_file
    ]

    verdict = Verdict.NO_SOSTENIBLE if evidence else Verdict.APROBADO
    return VerifierResult(verdict=verdict, evidence=evidence)

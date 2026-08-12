from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum


class Verdict(str, Enum):
    APROBADO = "APROBADO"
    APROBADO_CON_OBSERVACIONES = "APROBADO_CON_OBSERVACIONES"
    NO_SOSTENIBLE = "NO_SOSTENIBLE"


_VERDICT_SEVERITY = {
    Verdict.APROBADO: 0,
    Verdict.APROBADO_CON_OBSERVACIONES: 1,
    Verdict.NO_SOSTENIBLE: 2,
}


def worst_verdict(verdicts: Iterable[Verdict]) -> Verdict:
    return max(verdicts, key=lambda v: _VERDICT_SEVERITY[v], default=Verdict.APROBADO)


@dataclass(frozen=True)
class Evidence:
    file: str
    line: int
    note: str


@dataclass(frozen=True)
class VerifierResult:
    verdict: Verdict
    evidence: list[Evidence] = field(default_factory=list)


@dataclass(frozen=True)
class AuditReport:
    final_verdict: Verdict
    verifier_results: dict[str, VerifierResult]
    skipped_verifiers: list[str] = field(default_factory=list)

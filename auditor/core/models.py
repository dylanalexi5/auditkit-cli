from dataclasses import dataclass, field
from enum import Enum


class Verdict(str, Enum):
    APROBADO = "APROBADO"
    APROBADO_CON_OBSERVACIONES = "APROBADO_CON_OBSERVACIONES"
    NO_SOSTENIBLE = "NO_SOSTENIBLE"


@dataclass(frozen=True)
class Evidence:
    file: str
    line: int
    note: str


@dataclass(frozen=True)
class VerifierResult:
    verdict: Verdict
    evidence: list[Evidence] = field(default_factory=list)

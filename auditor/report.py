import dataclasses
import json

from auditor.core.models import AuditReport, Verdict

_VERDICT_EMOJI = {
    Verdict.APROBADO: "✅",
    Verdict.APROBADO_CON_OBSERVACIONES: "\U0001f504",
    # ❌ y no ⏳: el reloj de arena se lee como "pendiente" o "en proceso", que
    # es lo contrario de lo que significa el peor veredicto posible.
    Verdict.NO_SOSTENIBLE: "❌",
}


def to_markdown(report: AuditReport, repo_label: str) -> str:
    lines = [
        f"# Reporte de auditoría — {repo_label}",
        "",
        f"**Veredicto final:** {_VERDICT_EMOJI[report.final_verdict]} {report.final_verdict.value}",
        "",
    ]

    for name, result in report.verifier_results.items():
        emoji = _VERDICT_EMOJI[result.verdict]
        lines.append(f"## {name} {emoji} {result.verdict.value}")
        if result.evidence:
            for item in result.evidence:
                lines.append(f"- {item.file}:{item.line} — {item.note}")
        else:
            lines.append("- (sin evidencia)")
        lines.append("")

    for name in report.skipped_verifiers:
        lines.append(f"## {name} ⏸️ no ejecutado")
        lines.append("- pasá --run-tests (o confirmá interactivamente) para incluirlo")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def to_json(report: AuditReport, repo_label: str) -> str:
    payload = {
        "repo": repo_label,
        "final_verdict": report.final_verdict.value,
        "verifiers": {
            name: dataclasses.asdict(result) for name, result in report.verifier_results.items()
        },
        "skipped_verifiers": report.skipped_verifiers,
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)

from auditor.core.models import AuditReport, Evidence, Verdict, VerifierResult
from auditor.report import to_markdown


def _report(verdict: Verdict) -> AuditReport:
    return AuditReport(
        final_verdict=verdict,
        verifier_results={
            "secrets": VerifierResult(
                verdict=verdict, evidence=[Evidence(file="app.py", line=1, note="AWS Key")]
            )
        },
    )


def test_no_sostenible_se_marca_como_reprobado() -> None:
    markdown = to_markdown(_report(Verdict.NO_SOSTENIBLE), "demo")

    assert "❌" in markdown
    assert "⏳" not in markdown, "el reloj de arena se lee como 'pendiente', no como reprobado"


def test_aprobado_y_observaciones_conservan_su_marca() -> None:
    assert "✅" in to_markdown(_report(Verdict.APROBADO), "demo")
    assert "\U0001f504" in to_markdown(_report(Verdict.APROBADO_CON_OBSERVACIONES), "demo")


def test_verificador_no_ejecutado_no_usa_la_marca_de_reprobado() -> None:
    report = AuditReport(
        final_verdict=Verdict.APROBADO, verifier_results={}, skipped_verifiers=["build_check"]
    )

    markdown = to_markdown(report, "demo")

    assert "build_check" in markdown
    assert "❌" not in markdown

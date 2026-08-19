import json
from pathlib import Path

import pytest

from auditor import cli
from auditor.cli import InvalidUrlError, _validate_github_url
from auditor.core.models import Verdict, VerifierResult


def test_validate_github_url_accepts_valid_url() -> None:
    url = "https://github.com/octocat/Hello-World"
    assert _validate_github_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "ext::sh -c touch pwned",
        "https://evil.com/octocat/Hello-World",
        "https://user:token@github.com/octocat/Hello-World",
        "file:///etc/passwd",
        "--upload-pack=touch pwned",
        "git@github.com:octocat/Hello-World.git",
    ],
)
def test_validate_github_url_rejects_dangerous_input(url: str) -> None:
    with pytest.raises(InvalidUrlError):
        _validate_github_url(url)


def _fake_clone(_url: str, dest: Path) -> None:
    dest.mkdir(parents=True)
    (dest / "README.md").write_text("# demo\n")


def test_main_semantic_flag_merges_result_into_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(cli, "_clone", _fake_clone)
    monkeypatch.setattr(
        cli.semantic_check,
        "verify",
        lambda ctx, other_results: VerifierResult(Verdict.APROBADO_CON_OBSERVACIONES, []),
    )

    exit_code = cli.main(["https://github.com/octocat/Hello-World", "--semantic"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "semantic_check" in output
    assert "APROBADO_CON_OBSERVACIONES" in output


def test_main_without_semantic_flag_never_calls_semantic_check(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    def _boom(*args, **kwargs):
        raise AssertionError("no deberia llamar a semantic_check sin --semantic")

    monkeypatch.setattr(cli, "_clone", _fake_clone)
    monkeypatch.setattr(cli.semantic_check, "verify", _boom)

    exit_code = cli.main(["https://github.com/octocat/Hello-World"])

    assert exit_code == 0
    assert "semantic_check" not in capsys.readouterr().out


def test_main_without_triage_flag_never_calls_the_agent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Opt-in real: sin --triage no se toca la API de Groq (mismo criterio
    que --semantic y --run-tests, ADR 0003)."""

    def _boom(*args, **kwargs):
        raise AssertionError("no deberia llamar al agente sin --triage")

    monkeypatch.setattr(cli, "_clone", _fake_clone)
    monkeypatch.setattr(cli.triage_agent, "triage", _boom)

    assert cli.main(["https://github.com/octocat/Hello-World"]) == 0


def test_main_triage_flag_runs_the_agent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    llamado = {}

    def _fake_triage(repo_path, results, **kwargs):
        llamado["si"] = True
        return results

    monkeypatch.setattr(cli, "_clone", _fake_clone)
    monkeypatch.setattr(cli.triage_agent, "triage", _fake_triage)

    assert cli.main(["https://github.com/octocat/Hello-World", "--triage"]) == 0
    assert llamado.get("si")


def test_main_triage_failure_does_not_kill_the_report(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Si el agente explota, el reporte sale igual con lo que ya verificaron
    los verificadores deterministas (ADR 0003, Mitigacion 4)."""

    def _explota(*args, **kwargs):
        raise RuntimeError("el agente se rompio")

    monkeypatch.setattr(cli, "_clone", _fake_clone)
    monkeypatch.setattr(cli.triage_agent, "triage", _explota)

    exit_code = cli.main(["https://github.com/octocat/Hello-World", "--triage"])

    assert exit_code == 0
    assert "secrets" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# --ask (ADR 0005): exploratorio, separado del reporte, sin veredicto.
# ---------------------------------------------------------------------------


def _fake_clone_con_codigo(_url: str, dest: Path) -> None:
    dest.mkdir(parents=True)
    (dest / "README.md").write_text("# demo\n")
    (dest / "mod.py").write_text("def reintentar(req):\n    pass\n", encoding="utf-8")


def _encoder_constante(_textos):
    import numpy as np

    return np.vstack([np.array([1.0, 0.0], dtype="float32") for _ in _textos])


def test_ask_no_corre_los_verificadores(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """La pregunta es exploratoria: pagar pip-audit y el scan de secretos
    para responderla seria gasto puro. Si algun verificador corriera, este
    test explota."""
    monkeypatch.setattr(cli, "_clone", _fake_clone_con_codigo)
    monkeypatch.setattr(cli, "_encoder_de_ask", lambda: _encoder_constante)
    for modulo in (cli.secrets, cli.readme_check, cli.deps_check):
        monkeypatch.setattr(
            modulo,
            "verify",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("--ask no debe correr verificadores")
            ),
        )

    exit_code = cli.main(["https://github.com/octocat/Hello-World", "--ask", "reintentos"])

    assert exit_code == 0


def test_ask_imprime_ubicacion_y_la_advertencia(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(cli, "_clone", _fake_clone_con_codigo)
    monkeypatch.setattr(cli, "_encoder_de_ask", lambda: _encoder_constante)

    exit_code = cli.main(["https://github.com/octocat/Hello-World", "--ask", "reintentos"])

    salida = capsys.readouterr().out
    assert exit_code == 0
    assert "mod.py:1" in salida
    assert "no te dice si algo es cierto" in salida


def test_ask_no_emite_veredicto_ni_secciones_del_reporte(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """No aparece en el reporte de auditoria: ni veredicto final, ni las
    secciones de los verificadores."""
    monkeypatch.setattr(cli, "_clone", _fake_clone_con_codigo)
    monkeypatch.setattr(cli, "_encoder_de_ask", lambda: _encoder_constante)

    cli.main(["https://github.com/octocat/Hello-World", "--ask", "reintentos"])

    salida = capsys.readouterr().out
    assert "Veredicto final" not in salida
    assert "APROBADO" not in salida
    assert "deps_check" not in salida


def test_ask_con_el_modelo_no_disponible_falla_explicito(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Sin modelo no hay respuesta. Fingir una lista vacia seria peor que
    fallar: se leeria como "no hay nada que ver aca"."""
    monkeypatch.setattr(cli, "_clone", _fake_clone_con_codigo)

    def _sin_modelo():
        raise cli.ModeloNoDisponibleError("no se pudo cargar el modelo")

    monkeypatch.setattr(cli, "_encoder_de_ask", _sin_modelo)

    exit_code = cli.main(["https://github.com/octocat/Hello-World", "--ask", "x"])

    assert exit_code == 1
    assert "no se pudo cargar el modelo" in capsys.readouterr().err


def test_ask_con_json_devuelve_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(cli, "_clone", _fake_clone_con_codigo)
    monkeypatch.setattr(cli, "_encoder_de_ask", lambda: _encoder_constante)

    cli.main(["https://github.com/octocat/Hello-World", "--ask", "x", "--json"])

    datos = json.loads(capsys.readouterr().out)
    assert datos["pregunta"] == "x"
    assert datos["fragmentos"][0]["file"] == "mod.py"

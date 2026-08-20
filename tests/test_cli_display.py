"""Tests de la capa de presentacion (ADR 0006).

La regla que fija este archivo es una sola, y es la que hace que la capa sea
segura: **presentacion no puede cambiar el contenido**. El veredicto que entra
es el que sale, la evidencia que entra es la que se muestra, y sin terminal el
reporte es el markdown de siempre byte por byte.
"""

import io

import pytest
from rich.console import Console

from auditor import cli_display
from auditor import report as report_mod
from auditor.core.models import AuditReport, Evidence, Verdict, VerifierResult


def _reporte() -> AuditReport:
    return AuditReport(
        final_verdict=Verdict.NO_SOSTENIBLE,
        verifier_results={
            "secrets": VerifierResult(verdict=Verdict.APROBADO, evidence=[]),
            "deps_check": VerifierResult(
                verdict=Verdict.NO_SOSTENIBLE,
                evidence=[
                    Evidence(file="requirements.txt", line=1, note="'x' sin declarar"),
                    Evidence(file="requirements.txt", line=2, note="'y' sin usar"),
                ],
            ),
        },
        skipped_verifiers=["build_check"],
    )


def _display(*, rico: bool, con_progreso: bool = True):
    """Display con consolas de mentira, para poder leer lo que escribio."""
    salida = io.StringIO()
    progreso = io.StringIO()
    display = cli_display.Display(
        salida=Console(file=salida, force_terminal=rico, width=100, no_color=not rico),
        progreso=(
            Console(file=progreso, force_terminal=True, width=100)
            if con_progreso
            else None
        ),
        rico=rico,
    )
    return display, salida, progreso


def test_sin_terminal_el_reporte_es_el_markdown_de_siempre(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No "parecido": identico. Es lo que hace que los tests que ya existen
    sobre `to_markdown` sigan siendo validos despues de esta capa.

    Se lee con `capsys` y no con la consola de mentira a proposito: el camino
    plano usa `print()` crudo, sin pasar por rich, justamente para que no
    haya forma de que rich reinterprete un `[...]` de una nota como markup."""
    reporte = _reporte()
    display, _, _ = _display(rico=False)

    display.reporte(reporte, "https://github.com/demo/demo")

    esperado = report_mod.to_markdown(reporte, "https://github.com/demo/demo") + "\n"
    assert capsys.readouterr().out == esperado


def test_sin_terminal_no_hay_un_solo_escape_ansi(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Un escape ANSI dentro de un archivo redirigido no se ve cuando lo
    mirás con `cat` en la misma terminal que lo genero. Se busca explicito."""
    display, _, _ = _display(rico=False)

    display.reporte(_reporte(), "https://github.com/demo/demo")

    salida = capsys.readouterr().out
    assert salida != ""
    assert "\x1b[" not in salida


def test_con_terminal_el_reporte_lleva_color() -> None:
    display, salida, _ = _display(rico=True)

    display.reporte(_reporte(), "https://github.com/demo/demo")

    assert "\x1b[" in salida.getvalue()


def test_el_reporte_rico_muestra_todos_los_verificadores_y_su_veredicto() -> None:
    """Un reporte mas lindo que muestra menos es peor que uno feo."""
    display, salida, _ = _display(rico=True)

    display.reporte(_reporte(), "https://github.com/demo/demo")

    texto = salida.getvalue()
    for esperado in ("secrets", "deps_check", "build_check", "NO_SOSTENIBLE", "APROBADO"):
        assert esperado in texto


def test_el_reporte_rico_no_esconde_ninguna_evidencia() -> None:
    display, salida, _ = _display(rico=True)

    display.reporte(_reporte(), "https://github.com/demo/demo")

    texto = salida.getvalue()
    assert "'x' sin declarar" in texto
    assert "'y' sin usar" in texto


def test_una_nota_multilinea_se_recorta_pero_dice_cuanto() -> None:
    """`build_check` mete la salida entera de pytest en la nota. La capa la
    recorta —no puede tocar el verificador— y declara lo que no muestra."""
    reporte = AuditReport(
        final_verdict=Verdict.NO_SOSTENIBLE,
        verifier_results={
            "build_check": VerifierResult(
                verdict=Verdict.NO_SOSTENIBLE,
                evidence=[
                    Evidence(
                        file="pytest",
                        line=0,
                        note="pytest fallo (exit 1): " + "\n".join(f"linea {n}" for n in range(20)),
                    )
                ],
            )
        },
        skipped_verifiers=[],
    )
    display, salida, _ = _display(rico=True)

    display.reporte(reporte, "https://github.com/demo/demo")

    texto = salida.getvalue()
    assert "pytest fallo (exit 1)" in texto
    assert "linea 19" not in texto
    assert "--json" in texto


def test_con_progreso_devuelve_el_mismo_resultado_sin_tocarlo() -> None:
    """La capa decora, no interviene: el objeto que devuelve el verificador
    es el mismo objeto que recibe quien lo llamo."""
    esperado = VerifierResult(verdict=Verdict.APROBADO_CON_OBSERVACIONES, evidence=[])
    display, _, _ = _display(rico=True)

    envuelto = display.con_progreso("demo", lambda ctx: esperado)

    assert envuelto(None) is esperado


def test_el_progreso_no_escribe_una_sola_letra_en_la_salida() -> None:
    """Si el progreso cayera en stdout, `> salida.txt` guardaria el spinner."""
    display, salida, progreso = _display(rico=True)

    display.con_progreso("demo", lambda ctx: VerifierResult(Verdict.APROBADO, []))(None)

    assert salida.getvalue() == ""
    assert "demo" in progreso.getvalue()


def test_el_progreso_muestra_la_marca_del_veredicto() -> None:
    display, _, progreso = _display(rico=True)

    display.con_progreso("demo", lambda ctx: VerifierResult(Verdict.NO_SOSTENIBLE, []))(None)

    assert "demo" in progreso.getvalue()
    assert "NO_SOSTENIBLE" in progreso.getvalue()


def test_sin_progreso_el_verificador_igual_corre_y_no_se_escribe_nada() -> None:
    """`--json` y la salida a un pipe apagan el progreso entero."""
    display, salida, _ = _display(rico=False, con_progreso=False)
    esperado = VerifierResult(verdict=Verdict.APROBADO, evidence=[])

    assert display.con_progreso("demo", lambda ctx: esperado)(None) is esperado
    assert salida.getvalue() == ""


def test_para_json_no_pinta_ni_muestra_progreso(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--json` es una interfaz de maquina: un solo escape ANSI la rompe."""
    monkeypatch.setattr(cli_display.sys.stdout, "isatty", lambda: True, raising=False)
    monkeypatch.setattr(cli_display.sys.stderr, "isatty", lambda: True, raising=False)

    display = cli_display.Display.para(json_mode=True)

    assert display.rico is False
    assert display.progreso is None


def test_sin_terminal_no_se_arma_progreso(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_display.sys.stdout, "isatty", lambda: False, raising=False)
    monkeypatch.setattr(cli_display.sys.stderr, "isatty", lambda: False, raising=False)

    display = cli_display.Display.para(json_mode=False)

    assert display.rico is False
    assert display.progreso is None


def test_stdout_pipeado_con_stderr_en_terminal_deja_progreso_pero_no_color(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El caso de `> salida.txt` en una terminal: el archivo tiene que salir
    en texto plano y el spinner tiene que seguir viendose."""
    monkeypatch.setattr(cli_display.sys.stdout, "isatty", lambda: False, raising=False)
    monkeypatch.setattr(cli_display.sys.stderr, "isatty", lambda: True, raising=False)

    display = cli_display.Display.para(json_mode=False)

    assert display.rico is False
    assert display.progreso is not None


def test_un_verificador_salteado_se_anuncia() -> None:
    """Sin esto, `build_check` simplemente no aparece y el usuario no sabe
    si se salteo o si nunca existio."""
    display, _, progreso = _display(rico=True)

    display.salteado("build_check")

    assert "build_check" in progreso.getvalue()
    assert "no ejecutado" in progreso.getvalue()


def test_un_paso_sin_veredicto_propio_se_anuncia_igual() -> None:
    """El triage revisa hallazgos ajenos y no produce los suyos, pero es de
    los pasos mas lentos: no anunciarlo deja el proceso mudo un minuto."""
    display, _, progreso = _display(rico=True)

    display.hecho("triage")

    assert "triage" in progreso.getvalue()


def test_sin_progreso_ni_salteado_ni_hecho_escriben_nada() -> None:
    display, salida, _ = _display(rico=False, con_progreso=False)

    display.salteado("build_check")
    display.hecho("triage")
    display.termina("secrets", Verdict.APROBADO)

    assert salida.getvalue() == ""


def _con_nota(nota: str) -> AuditReport:
    return AuditReport(
        final_verdict=Verdict.NO_SOSTENIBLE,
        verifier_results={
            "build_check": VerifierResult(
                verdict=Verdict.NO_SOSTENIBLE,
                evidence=[Evidence(file="pytest", line=0, note=nota)],
            )
        },
        skipped_verifiers=[],
    )


def _render(reporte: AuditReport) -> str:
    display, salida, _ = _display(rico=True)
    display.reporte(reporte, "https://github.com/demo/demo")
    return salida.getvalue()


def test_una_nota_corta_no_se_recorta() -> None:
    """Tres lineas entran enteras. El literal va explicito, no
    `cli_display._MAX_LINEAS_NOTA`: comparar contra la misma constante que se
    esta probando pasa con cualquier valor."""
    texto = _render(_con_nota("uno\ndos\ntres"))

    assert "(+" not in texto
    assert "tres" in texto


def test_una_nota_con_una_linea_de_mas_lo_dice_en_singular() -> None:
    texto = _render(_con_nota("uno\ndos\ntres\ncuatro"))

    assert "+1 línea " in texto
    assert "+1 líneas" not in texto
    assert "cuatro" not in texto


def test_una_nota_con_varias_de_mas_cuenta_bien_cuantas() -> None:
    """El `- _MAX_LINEAS_NOTA` de la resta sobrevivia mutado a `+`, `*` y
    todo lo demas: sin un numero exacto esperado, cualquier cuenta pasa."""
    texto = _render(_con_nota("\n".join(f"linea {n}" for n in range(9))))

    assert "+6 líneas" in texto


def test_el_nombre_del_verificador_aparece_una_sola_vez() -> None:
    """Con dos evidencias, el nombre y el veredicto van en la primera fila y
    las demas quedan vacias. `indice == 0` mutado a `!=`, `>` o `<=` repetia
    el nombre en cada fila o lo escondia entero."""
    reporte = AuditReport(
        final_verdict=Verdict.NO_SOSTENIBLE,
        verifier_results={
            "deps_check": VerifierResult(
                verdict=Verdict.NO_SOSTENIBLE,
                evidence=[
                    Evidence(file="a.txt", line=1, note="primera"),
                    Evidence(file="b.txt", line=2, note="segunda"),
                    Evidence(file="c.txt", line=3, note="tercera"),
                ],
            )
        },
        skipped_verifiers=[],
    )

    texto = _render(reporte)

    assert texto.count("deps_check") == 1
    assert texto.count("NO_SOSTENIBLE") == 2  # el del panel final y el de la fila
    for nota in ("primera", "segunda", "tercera"):
        assert nota in texto

"""Tests del comando --ask (ADR 0005).

Ninguno carga el modelo real: el encoder viene inyectado, mismo patron que
`client=` en triage_agent.py y `encoder=` en embedding_index.py.
"""

import dataclasses
import json
from pathlib import Path

import numpy as np
import pytest

from auditor import ask
from auditor.core import embedding_index


def _encoder_falso(mapa: dict[str, list[float]]):
    def encode(textos: list[str]):
        return np.vstack([np.array(mapa[t], dtype="float32") for t in textos])

    return encode


def test_buscar_devuelve_los_fragmentos_ordenados_por_similitud(
    tmp_path: Path,
) -> None:
    (tmp_path / "mod.py").write_text(
        "def reintentar(req):\n    pass\n\n\ndef formatear(x):\n    pass\n",
        encoding="utf-8",
    )
    encoder = _encoder_falso(
        {
            "reintentos": [1.0, 0.0],
            "def reintentar(req):\n    pass": [1.0, 0.0],
            "def formatear(x):\n    pass": [0.0, 1.0],
        }
    )

    respuesta = ask.buscar(tmp_path, "reintentos", top_n=5, encoder=encoder)

    assert [f.firma for f, _ in respuesta.fragmentos] == [
        "def reintentar(req):",
        "def formatear(x):",
    ]
    assert respuesta.fragmentos[0][1] == pytest.approx(1.0, abs=1e-6)


def test_buscar_en_un_repo_sin_codigo_no_toca_el_modelo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Carga perezosa: un repo sin un solo fragmento indexable no paga los
    7.6s del modelo. Mismo criterio con el que el agente de triage no gasto
    una llamada de API en pallets/click."""

    def _boom():
        raise AssertionError("no deberia cargar el modelo sin fragmentos")

    monkeypatch.setattr(embedding_index, "_cargar_encoder", _boom)
    (tmp_path / "README.md").write_text("# sin codigo\n", encoding="utf-8")

    respuesta = ask.buscar(tmp_path, "lo que sea", top_n=5)

    assert respuesta.fragmentos == ()


def test_el_texto_de_salida_dice_que_no_juzga(tmp_path: Path) -> None:
    """La advertencia no es decorativa: es la diferencia entre esto y el
    Paso 2 que el ADR 0003 descarto. Si el usuario lee la lista como un
    veredicto, volvimos al error medido."""
    (tmp_path / "mod.py").write_text("def f():\n    pass\n", encoding="utf-8")
    encoder = _encoder_falso({"q": [1.0, 0.0], "def f():\n    pass": [1.0, 0.0]})

    salida = ask.to_texto(ask.buscar(tmp_path, "q", top_n=5, encoder=encoder))

    assert "no te dice si algo es cierto" in salida


def test_el_texto_de_salida_cita_archivo_y_linea(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text("\n\ndef enviar():\n    pass\n", encoding="utf-8")
    encoder = _encoder_falso({"q": [1.0, 0.0], "def enviar():\n    pass": [1.0, 0.0]})

    salida = ask.to_texto(ask.buscar(tmp_path, "q", top_n=5, encoder=encoder))

    assert "mod.py:3" in salida
    assert "def enviar():" in salida


def test_el_texto_de_salida_no_inventa_un_porcentaje(tmp_path: Path) -> None:
    """La similitud es una distancia coseno, no una probabilidad de que la
    respuesta sea correcta. Mostrarla como porcentaje invita a leerla como lo
    segundo."""
    (tmp_path / "mod.py").write_text("def f():\n    pass\n", encoding="utf-8")
    encoder = _encoder_falso({"q": [1.0, 0.0], "def f():\n    pass": [0.0, 1.0]})

    salida = ask.to_texto(ask.buscar(tmp_path, "q", top_n=5, encoder=encoder))

    assert "%" not in salida


def test_el_texto_declara_cuando_el_corpus_quedo_truncado(tmp_path: Path) -> None:
    """Con el corpus cortado, "no aparece en los resultados" deja de
    significar "no esta en el repo"."""
    (tmp_path / "mod.py").write_text(
        "".join(f"def f{i}():\n    pass\n" for i in range(5)), encoding="utf-8"
    )
    encoder = _encoder_falso(
        {"q": [1.0, 0.0], **{f"def f{i}():\n    pass": [1.0, 0.0] for i in range(5)}}
    )

    respuesta = ask.buscar(tmp_path, "q", top_n=5, max_fragmentos=2, encoder=encoder)
    salida = ask.to_texto(respuesta)

    assert respuesta.truncado is True
    assert "truncado" in salida.lower()


def test_sin_resultados_lo_dice_en_vez_de_imprimir_una_lista_vacia(
    tmp_path: Path,
) -> None:
    (tmp_path / "README.md").write_text("# sin codigo\n", encoding="utf-8")

    salida = ask.to_texto(ask.buscar(tmp_path, "q", top_n=5))

    assert "no se encontró código indexable" in salida


def test_la_salida_json_lleva_ubicacion_y_similitud(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text("def enviar():\n    pass\n", encoding="utf-8")
    encoder = _encoder_falso({"q": [1.0, 0.0], "def enviar():\n    pass": [1.0, 0.0]})

    datos = json.loads(ask.to_json(ask.buscar(tmp_path, "q", top_n=5, encoder=encoder)))

    assert datos["pregunta"] == "q"
    assert datos["fragmentos"][0]["file"] == "mod.py"
    assert datos["fragmentos"][0]["line"] == 1
    assert datos["fragmentos"][0]["similitud"] == pytest.approx(1.0, abs=1e-6)
    assert "no te dice si algo es cierto" in datos["advertencia"]


# ---------------------------------------------------------------------------
# Huecos que encontro el mutation testing.
# ---------------------------------------------------------------------------


def test_la_respuesta_es_inmutable(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text("def f():\n    pass\n", encoding="utf-8")
    encoder = _encoder_falso({"q": [1.0, 0.0], "def f():\n    pass": [1.0, 0.0]})

    respuesta = ask.buscar(tmp_path, "q", top_n=1, encoder=encoder)

    with pytest.raises(dataclasses.FrozenInstanceError):
        respuesta.truncado = True  # type: ignore[misc]


def test_por_defecto_devuelve_cinco_fragmentos(tmp_path: Path) -> None:
    """`_TOP_N = 5` -> 4 / 6 sobrevivia: todos los tests pasaban `top_n`
    explicito, asi que el default no se ejercitaba nunca. Literal 5 a
    proposito, no ask._TOP_N: comparar contra la constante que el mutante
    altera es tautologico."""
    (tmp_path / "mod.py").write_text(
        "".join(f"def f{i}():\n    pass\n\n\n" for i in range(8)), encoding="utf-8"
    )
    encoder = _encoder_falso(
        {"q": [1.0, 0.0], **{f"def f{i}():\n    pass": [1.0, 0.0] for i in range(8)}}
    )

    respuesta = ask.buscar(tmp_path, "q", encoder=encoder)

    assert len(respuesta.fragmentos) == 5


def test_un_repo_con_codigo_pero_sin_definiciones_lo_dice_distinto(
    tmp_path: Path,
) -> None:
    """`archivos_leidos == 0` -> `>= 0` sobrevivia. Son dos situaciones
    distintas y el mensaje tiene que distinguirlas: "no hay codigo que
    indexar" no es lo mismo que "hay codigo pero no hay ninguna funcion"."""
    (tmp_path / "mod.py").write_text("CONSTANTE = 1\n", encoding="utf-8")

    respuesta = ask.buscar(tmp_path, "q", top_n=5)
    salida = ask.to_texto(respuesta)

    assert respuesta.archivos_leidos == 1
    assert respuesta.fragmentos == ()
    assert "no se encontró código indexable" not in salida
    assert "ningún fragmento" in salida


def test_el_json_no_escapa_los_acentos(tmp_path: Path) -> None:
    r"""`ensure_ascii=False` -> `True` sobrevivia. Todo este proyecto reporta
    en español: con `True`, la advertencia y las preguntas salen como
    `\u00f3` y el JSON deja de ser legible para un humano."""
    (tmp_path / "mod.py").write_text("def f():\n    pass\n", encoding="utf-8")
    encoder = _encoder_falso(
        {"¿dónde?": [1.0, 0.0], "def f():\n    pass": [1.0, 0.0]}
    )

    crudo = ask.to_json(ask.buscar(tmp_path, "¿dónde?", top_n=1, encoder=encoder))

    assert "¿dónde?" in crudo
    assert "\\u" not in crudo

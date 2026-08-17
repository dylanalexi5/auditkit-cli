"""Tests del comando --ask (ADR 0005).

Ninguno carga el modelo real: el encoder viene inyectado, mismo patron que
`client=` en triage_agent.py y `encoder=` en embedding_index.py.
"""

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

"""Tests del cruce semántico afirmación↔evidencia (ADR 0003, Paso 1).

Ninguno carga el modelo real: el encoder viene inyectado, mismo patrón que
`client=` en triage_agent.py. El test que sí usa el modelo real vive aparte
y está marcado.
"""


import numpy as np
import pytest

from auditor.core import embedding_index


@pytest.fixture(autouse=True)
def _sin_modelo_cacheado():
    """El encoder se cachea a nivel modulo para no pagar 7.6s dos veces en la
    misma corrida. En los tests eso filtra estado entre casos: si un test
    carga el modelo real, los que sustituyen `_cargar_encoder` nunca lo ven
    llamar. Se limpia antes y despues de cada test."""
    embedding_index._encoder_cacheado = None
    yield
    embedding_index._encoder_cacheado = None


def _encoder_falso(mapa: dict[str, list[float]]):
    """Encoder determinista: cada texto conocido devuelve su vector fijo.

    Los vectores se normalizan, así el producto punto ES la similitud coseno
    y los umbrales del test son legibles a ojo.
    """

    def encode(textos: list[str]) -> np.ndarray:
        vectores = []
        for texto in textos:
            if texto not in mapa:
                raise AssertionError(f"el test no definió vector para: {texto!r}")
            v = np.array(mapa[texto], dtype="float32")
            vectores.append(v / np.linalg.norm(v))
        return np.vstack(vectores)

    return encode


# --- Carga perezosa: no se paga lo que no se usa ---------------------------


def test_no_carga_el_modelo_si_no_hay_afirmaciones() -> None:
    """El costo fijo del modelo son ~7.6s. Un repo sin afirmaciones no debe
    pagarlos - mismo criterio que el agente de triage, que no gastó una sola
    llamada de API en pallets/click."""

    def _explota(*args, **kwargs):
        raise AssertionError("no debería cargar el modelo sin afirmaciones")

    assert embedding_index.cruzar([], ["una nota"], encoder=_explota) == []


def test_no_carga_el_modelo_si_no_hay_notas() -> None:
    def _explota(*args, **kwargs):
        raise AssertionError("no debería cargar el modelo sin notas contra qué cruzar")

    assert embedding_index.cruzar(["una afirmacion"], [], encoder=_explota) == [None]


# --- El cruce en sí --------------------------------------------------------


def test_devuelve_el_indice_de_la_nota_mas_parecida() -> None:
    encoder = _encoder_falso(
        {
            "tiene tests": [1.0, 0.0],
            "no hay funciones de test": [0.99, 0.14],
            "dependencia sin declarar": [0.0, 1.0],
        }
    )

    resultado = embedding_index.cruzar(
        ["tiene tests"],
        ["dependencia sin declarar", "no hay funciones de test"],
        encoder=encoder,
    )

    assert resultado == [1]


def test_devuelve_none_si_ninguna_nota_supera_el_umbral() -> None:
    encoder = _encoder_falso(
        {
            "licencia MIT": [1.0, 0.0],
            "dependencia sin declarar": [0.0, 1.0],
        }
    )

    resultado = embedding_index.cruzar(
        ["licencia MIT"], ["dependencia sin declarar"], encoder=encoder
    )

    assert resultado == [None], "vectores ortogonales: similitud 0, nada que reportar"


def test_el_umbral_se_respeta_en_el_borde() -> None:
    """Dos vectores con similitud coseno exacta de 0.6."""
    encoder = _encoder_falso({"a": [1.0, 0.0], "b": [0.6, 0.8]})

    assert embedding_index.cruzar(["a"], ["b"], umbral=0.59, encoder=encoder) == [0]
    assert embedding_index.cruzar(["a"], ["b"], umbral=0.61, encoder=encoder) == [None]


def test_cada_afirmacion_se_evalua_por_separado() -> None:
    encoder = _encoder_falso(
        {
            "tiene tests": [1.0, 0.0],
            "licencia MIT": [0.0, 1.0],
            "no hay funciones de test": [1.0, 0.0],
        }
    )

    resultado = embedding_index.cruzar(
        ["tiene tests", "licencia MIT"], ["no hay funciones de test"], encoder=encoder
    )

    assert resultado == [0, None]


def test_elige_la_mas_parecida_no_la_primera_que_supera_el_umbral() -> None:
    encoder = _encoder_falso(
        {
            "cobertura de tests": [1.0, 0.0],
            "nota floja": [0.8, 0.6],
            "nota fuerte": [0.99, 0.14],
        }
    )

    resultado = embedding_index.cruzar(
        ["cobertura de tests"], ["nota floja", "nota fuerte"], encoder=encoder
    )

    assert resultado == [1]


# --- Degradación con gracia ------------------------------------------------


def test_modelo_no_instalado_lanza_error_tipado(monkeypatch: pytest.MonkeyPatch) -> None:
    """Quien llama decide qué hacer; este módulo no decide por él. El
    verificador cae al cruce de keywords y deja nota."""

    def _sin_libreria():
        raise ImportError("No module named 'sentence_transformers'")

    monkeypatch.setattr(embedding_index, "_cargar_encoder", _sin_libreria)

    with pytest.raises(embedding_index.ModeloNoDisponibleError):
        embedding_index.cruzar(["una afirmacion"], ["una nota"])


def test_fallo_de_descarga_lanza_error_tipado(monkeypatch: pytest.MonkeyPatch) -> None:
    """El modelo se baja de HuggingFace la primera vez. Ya se observó un
    fallo por TLS en este proyecto (ADR 0003, Mitigación 1): el RAG "local"
    no es local en la primera corrida."""

    def _falla_red():
        raise OSError("SSL: CERTIFICATE_VERIFY_FAILED")

    monkeypatch.setattr(embedding_index, "_cargar_encoder", _falla_red)

    with pytest.raises(embedding_index.ModeloNoDisponibleError):
        embedding_index.cruzar(["una afirmacion"], ["una nota"])


# --- Contra el modelo real -------------------------------------------------


@pytest.mark.slow
def test_modelo_real_cruza_ingles_con_espaniol() -> None:
    """EL punto del Paso 1, y el único par que la calibración demostró que
    el cruce de keywords se pierde y el semántico encuentra.

    "vulnerabilities" y "vulnerabilidades" no comparten token, así que la
    intersección de palabras clave da vacío — exactamente la ceguera entre
    idiomas que el ADR 0002 documenta. El modelo las cruza igual.

    (Se probó primero con "Black has 100% test coverage" contra la nota de
    cobertura, y NO pasa: queda en 0.297, apenas debajo del umbral. Ese par
    lo encuentra el cruce de keywords, porque comparten el token "test". Por
    eso el diseño final usa los dos mecanismos en unión y no reemplaza uno
    por otro.)
    """
    afirmacion = "No known security vulnerabilities"
    notas = [
        "'click' esta declarado pero no se usa en el codigo",
        "pyyaml 5.3 tiene vulnerabilidades conocidas: CVE-2020-14343",
    ]

    resultado = embedding_index.cruzar([afirmacion], notas)

    assert resultado == [1], (
        "el modelo real tiene que cruzar la afirmación en inglés con la nota "
        "en español sobre vulnerabilidades, no con la de dependencias"
    )


@pytest.mark.slow
def test_modelo_real_no_cruza_temas_sin_relacion() -> None:
    """Contracara del anterior: si baja el umbral hasta hacer que todo matchee
    con todo, el Paso 1 sería peor que el cruce de keywords."""
    resultado = embedding_index.cruzar(
        ["Black is licensed under MIT"],
        ["pip-audit no pudo completarse a tiempo - vulnerabilidades no verificadas"],
    )

    assert resultado == [None]

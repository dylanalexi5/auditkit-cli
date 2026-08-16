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


# --- Mutation-testing hardening -------------------------------------------
# 102 mutantes candidatos, 44 sobrevivientes en la primera vuelta, 35 tras
# esta bateria. De esos 35, 33 son anotaciones `X | None` (PEP 649: Python
# 3.14 nunca las evalua) y 2 son el mismo caso, verificado empiricamente:
#
#   `norm + 1e-12` mutado a `norm - 1e-12`, en las dos normalizaciones.
#
# Primera lectura: parecia matable, porque con un vector de norma 1e-20 el
# denominador se vuelve negativo y el vector se da vuelta ([1e-08, 0] contra
# [-1e-08, 0]). Pero al escribir el test se ve que NO alcanza: el resultado
# no es el vector, es el producto punto, y ahi el signo se cancela porque se
# voltean los dos lados. Con solo un lado minusculo, la similitud queda del
# orden de 1e-08 - debajo de cualquier umbral con sentido - en las dos
# versiones. Y para normas normales la diferencia de 1e-12 queda muy por
# debajo de los ~7 digitos de float32.
#
# Equivalente, entonces. Pero por la razon correcta, no por la que parecia.


def _encoder_crudo(mapa: dict[str, list[float]]):
    """Encoder que NO normaliza, a diferencia de `_encoder_falso`.

    Ese detalle importa: si todos los vectores del test ya vienen
    normalizados, la normalizacion del modulo es un no-op y ningun test la
    ejercita. El modelo real devuelve vectores de norma arbitraria.
    """

    def encode(textos: list[str]):
        return np.vstack([np.array(mapa[t], dtype="float32") for t in textos])

    return encode


def test_los_vectores_se_normalizan_antes_de_comparar() -> None:
    """Sin normalizar, el producto punto premia al vector mas LARGO en vez
    de al mas parecido en direccion.

    "nota larga" apunta lejos (coseno 0.6) pero mide 10; "nota corta" apunta
    exactamente igual que la afirmacion (coseno 1.0) pero mide 1. Sin
    normalizacion gana la larga (6 contra 1), que es la respuesta
    equivocada.
    """
    encoder = _encoder_crudo(
        {
            "afirmacion": [1.0, 0.0],
            "nota larga": [6.0, 8.0],
            "nota corta": [1.0, 0.0],
        }
    )

    resultado = embedding_index.cruzar(
        ["afirmacion"], ["nota larga", "nota corta"], encoder=encoder
    )

    assert resultado == [1], "tiene que ganar la mas parecida, no la de mayor norma"


def test_la_similitud_exacta_al_umbral_cuenta_como_relacionada() -> None:
    """Borde exacto: `>=`, no `>`. Un par que da justo el umbral es tan
    relacionado como uno que lo pasa por poco, y dejarlo afuera seria una
    decision arbitraria escondida en un operador.

    Se usan vectores IDENTICOS y umbral 1.0 porque es el unico punto donde
    float32 da el borde exacto: con [1,0] contra [0.6,0.8] y umbral 0.6, la
    similitud calculada es 0.6000000238418579 y los dos operadores coinciden
    - el test no distinguiria nada.
    """
    encoder = _encoder_crudo({"a": [1.0, 0.0], "b": [1.0, 0.0]})

    assert embedding_index.cruzar(["a"], ["b"], umbral=1.0, encoder=encoder) == [0]


def test_cada_fila_se_normaliza_por_su_propia_norma() -> None:
    """`keepdims=True` es lo que hace que cada vector se divida por SU norma.

    Sin eso, la division broadcastea por columna en vez de por fila: cada
    componente se divide por la norma de otro vector. Hacen falta dos
    afirmaciones de normas DISTINTAS para que se note - con normas iguales
    el resultado coincide por casualidad y el test no prueba nada.
    """
    encoder = _encoder_crudo(
        {
            "larga": [30.0, 40.0],  # norma 50
            "corta": [0.0, 1.0],  # norma 1
            "nota": [1.0, 0.0],
        }
    )

    resultado = embedding_index.cruzar(
        ["larga", "corta"], ["nota"], umbral=0.5, encoder=encoder
    )

    assert resultado == [0, None], (
        "'larga' apunta a 0.6 de la nota y 'corta' es ortogonal; si cada fila "
        "no se divide por su propia norma, los dos valores salen mal"
    )


def test_cada_afirmacion_se_normaliza_por_su_propia_norma() -> None:
    """Igual que el test de las notas, pero del lado de las afirmaciones.

    Hace falta que la NOTA ejercite la segunda componente: con una nota
    [1, 0] solo pesa la primera, que en el caso mutado se divide igual por
    la norma correcta, y el error se cancela por casualidad.

    Con keepdims=False, la fila 'a' queda [3/5, 4/2] = [0.6, 2.0] en vez de
    [0.6, 0.8]: su similitud contra [0, 1] salta de 0.8 a 2.0 y cruza el
    umbral que no deberia cruzar.
    """
    encoder = _encoder_crudo(
        {
            "a": [3.0, 4.0],  # norma 5
            "b": [0.0, 2.0],  # norma 2
            "nota": [0.0, 1.0],
        }
    )

    resultado = embedding_index.cruzar(
        ["a", "b"], ["nota"], umbral=0.9, encoder=encoder
    )

    assert resultado == [None, 0], (
        "'a' esta a 0.8 de la nota (debajo de 0.9) y 'b' a 1.0"
    )


def test_la_norma_de_la_afirmacion_no_infla_la_similitud() -> None:
    """La afirmacion tambien se normaliza, no solo las notas. Sin eso, una
    afirmacion de norma grande empuja TODAS sus similitudes por encima del
    umbral, y el umbral deja de significar algo."""
    encoder = _encoder_crudo({"a": [10.0, 0.0], "n": [0.6, 0.8]})

    resultado = embedding_index.cruzar(["a"], ["n"], umbral=0.9, encoder=encoder)

    assert resultado == [None], (
        "la similitud real es 0.6, por debajo de 0.9: la norma 10 de la "
        "afirmacion no puede convertirla en un match"
    )


def test_un_vector_nulo_no_produce_nan(monkeypatch: pytest.MonkeyPatch) -> None:
    """El `+ 1e-12` del denominador existe para esto. Sin el, un vector de
    norma cero da 0/0 = NaN, y NaN >= umbral es False en silencio: el
    hallazgo desaparece sin que nadie se entere."""
    encoder = _encoder_crudo({"afirmacion": [0.0, 0.0], "nota": [1.0, 0.0]})

    resultado = embedding_index.cruzar(["afirmacion"], ["nota"], encoder=encoder)

    assert resultado == [None], "sin relacion, pero por comparacion, no por NaN"


def test_el_encoder_real_pide_batch_y_sin_barra_de_progreso(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_cargar_encoder` solo se ejercita con el modelo real, que los tests
    rapidos no cargan. Se sustituye SentenceTransformer para cubrir los
    argumentos sin bajar 90MB: el batch es lo que hace que embeber 3033
    fragmentos tarde 25s y no varios minutos, y la barra de progreso
    ensuciaria la salida del reporte."""
    recibido = {}

    class _ModeloFalso:
        def __init__(self, nombre):
            recibido["modelo"] = nombre

        def encode(self, textos, **kwargs):
            recibido.update(kwargs)
            return np.vstack([np.array([1.0, 0.0], dtype="float32") for _ in textos])

    import sys
    import types

    modulo = types.ModuleType("sentence_transformers")
    modulo.SentenceTransformer = _ModeloFalso
    monkeypatch.setitem(sys.modules, "sentence_transformers", modulo)

    embedding_index.cruzar(["una afirmacion"], ["una nota"])

    assert recibido["modelo"] == "all-MiniLM-L6-v2"
    assert recibido["batch_size"] == 64
    assert recibido["show_progress_bar"] is False


def test_el_modelo_se_carga_una_sola_vez_por_proceso(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pagar 7.6s dos veces en la misma corrida tiraria el beneficio de la
    carga perezosa."""
    cargas = []

    def _contar():
        cargas.append(1)
        return _encoder_crudo({"a": [1.0, 0.0], "n": [1.0, 0.0]})

    monkeypatch.setattr(embedding_index, "_cargar_encoder", _contar)

    embedding_index.cruzar(["a"], ["n"])
    embedding_index.cruzar(["a"], ["n"])

    assert len(cargas) == 1


"""Tests de `rankear` (ADR 0005).

Casi ninguno carga el modelo real: el encoder viene inyectado, mismo patrón
que `client=` en triage_agent.py. Los dos que sí lo usan están marcados
`slow` y viven al final.

Este archivo perdió ~15 tests cuando se eliminó `cruzar()`. No se
"simplificaron": la función que probaban se borró, porque el ADR 0003 midió
que decidía mal y su `_UMBRAL` estaba calibrado para un modelo que ya no se
usa. Los que quedan cubren lo que sobrevivió — carga perezosa, degradación,
normalización y orden.
"""

import numpy as np
import pytest

from auditor.core import embedding_index


@pytest.fixture(autouse=True)
def _sin_modelo_cacheado():
    """El encoder se cachea a nivel módulo para no pagar el costo fijo dos
    veces en la misma corrida. En los tests eso filtra estado entre casos: si
    un test carga el modelo real, los que sustituyen `_cargar_encoder` nunca
    lo ven llamar. Se limpia antes y después de cada test."""
    embedding_index._encoder_cacheado = None
    yield
    embedding_index._encoder_cacheado = None


def _encoder_falso(mapa: dict[str, list[float]]):
    """Encoder determinista con vectores ya normalizados, para los tests
    donde lo que importa es el orden y no la normalización."""

    def encode(textos: list[str]):
        vectores = np.vstack([np.array(mapa[t], dtype="float32") for t in textos])
        return vectores / np.linalg.norm(vectores, axis=1, keepdims=True)

    return encode


def _encoder_crudo(mapa: dict[str, list[float]]):
    """Encoder que NO normaliza, a diferencia de `_encoder_falso`.

    Ese detalle importa y fue el gap más serio que encontró el mutation
    testing en su momento: si todos los vectores del test ya vienen
    normalizados, la normalización del módulo es un no-op y ningún test la
    ejercita. El modelo real devuelve vectores de norma arbitraria.
    """

    def encode(textos: list[str]):
        return np.vstack([np.array(mapa[t], dtype="float32") for t in textos])

    return encode


# --- Carga perezosa: no se paga lo que no se usa ---------------------------


def test_rankear_sin_candidatos_no_toca_el_modelo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un repo sin código indexable no paga el costo fijo del modelo. Mismo
    criterio con el que el agente de triage no gastó una llamada de API en
    pallets/click."""

    def _boom():
        raise AssertionError("no deberia cargar el modelo sin candidatos")

    monkeypatch.setattr(embedding_index, "_cargar_encoder", _boom)

    assert embedding_index.rankear("p", [], top_n=5) == []


def test_el_modelo_se_carga_una_sola_vez_por_proceso(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cargas = []

    def _contar():
        cargas.append(1)
        return _encoder_crudo({"p": [1.0, 0.0], "c": [1.0, 0.0]})

    monkeypatch.setattr(embedding_index, "_cargar_encoder", _contar)

    embedding_index.rankear("p", ["c"], top_n=1)
    embedding_index.rankear("p", ["c"], top_n=1)

    assert len(cargas) == 1


# --- El orden --------------------------------------------------------------


def test_rankear_ordena_por_similitud_descendente() -> None:
    encoder = _encoder_falso(
        {
            "pregunta": [1.0, 0.0],
            "lejos": [0.0, 1.0],
            "cerca": [1.0, 0.0],
            "medio": [1.0, 1.0],
        }
    )

    resultado = embedding_index.rankear(
        "pregunta", ["lejos", "cerca", "medio"], top_n=3, encoder=encoder
    )

    assert [indice for indice, _ in resultado] == [1, 2, 0]


def test_rankear_respeta_el_tope_de_resultados() -> None:
    encoder = _encoder_falso(
        {"p": [1.0, 0.0], "a": [1.0, 0.0], "b": [1.0, 1.0], "c": [0.0, 1.0]}
    )

    resultado = embedding_index.rankear("p", ["a", "b", "c"], top_n=2, encoder=encoder)

    assert len(resultado) == 2


def test_rankear_con_tope_mayor_que_los_candidatos_devuelve_todos() -> None:
    encoder = _encoder_falso({"p": [1.0, 0.0], "a": [1.0, 0.0], "b": [0.0, 1.0]})

    resultado = embedding_index.rankear("p", ["a", "b"], top_n=99, encoder=encoder)

    assert len(resultado) == 2


def test_rankear_no_aplica_umbral() -> None:
    """El único candidato es ortogonal a la pregunta (similitud 0.0) y aun así
    se devuelve. Es la diferencia de fondo con el `cruzar()` que se eliminó:
    rankear no decide si algo es relevante, lo ordena. Un umbral acá
    reintroduciría la clase de error que el ADR 0003 midió."""
    encoder = _encoder_falso({"p": [1.0, 0.0], "sin relacion": [0.0, 1.0]})

    resultado = embedding_index.rankear("p", ["sin relacion"], top_n=5, encoder=encoder)

    assert len(resultado) == 1
    assert resultado[0][1] == pytest.approx(0.0, abs=1e-6)


def test_rankear_devuelve_la_similitud_cruda_de_cada_candidato() -> None:
    encoder = _encoder_falso({"p": [1.0, 0.0], "identico": [1.0, 0.0]})

    resultado = embedding_index.rankear("p", ["identico"], top_n=1, encoder=encoder)

    assert resultado[0][1] == pytest.approx(1.0, abs=1e-6)


# --- Normalización ---------------------------------------------------------


def test_rankear_normaliza_los_vectores() -> None:
    """Sin normalizar, el producto punto premia al candidato más LARGO en vez
    del más parecido en dirección.

    "largo" apunta lejos (coseno 0.6) pero mide 10; "corto" apunta exactamente
    igual que la pregunta (coseno 1.0) pero mide 1. Sin normalización gana el
    largo (6 contra 1), que es la respuesta equivocada.
    """
    encoder = _encoder_crudo({"p": [1.0, 0.0], "largo": [6.0, 8.0], "corto": [1.0, 0.0]})

    resultado = embedding_index.rankear(
        "p", ["largo", "corto"], top_n=2, encoder=encoder
    )

    assert [indice for indice, _ in resultado] == [1, 0]
    assert resultado[0][1] == pytest.approx(1.0, abs=1e-6)
    assert resultado[1][1] == pytest.approx(0.6, abs=1e-6)


def test_cada_candidato_se_normaliza_por_su_propia_norma() -> None:
    """`keepdims=True` en la norma. Hacen falta DOS candidatos de normas
    distintas Y una segunda componente que se ejercite: con vectores tipo
    [1, 0] el error se cancela por casualidad."""
    encoder = _encoder_crudo(
        {"p": [0.6, 0.8], "corto": [0.6, 0.8], "largo": [60.0, 80.0]}
    )

    resultado = embedding_index.rankear(
        "p", ["corto", "largo"], top_n=2, encoder=encoder
    )

    # Los dos apuntan igual que la pregunta: normalizados, los dos dan 1.0.
    assert [round(s, 6) for _, s in resultado] == [1.0, 1.0]


def test_la_norma_de_la_consulta_no_infla_la_similitud() -> None:
    """La consulta también se normaliza. Con norma 10 y sin normalizar, la
    similitud daría 10 en vez de 1."""
    encoder = _encoder_crudo({"p": [10.0, 0.0], "c": [1.0, 0.0]})

    resultado = embedding_index.rankear("p", ["c"], top_n=1, encoder=encoder)

    assert resultado[0][1] == pytest.approx(1.0, abs=1e-6)


def test_un_vector_nulo_no_produce_nan() -> None:
    """El `1e-12` del denominador. Un vector nulo no debería ocurrir, pero
    costaría un NaN silencioso propagado al orden."""
    encoder = _encoder_crudo({"p": [0.0, 0.0], "c": [1.0, 0.0]})

    resultado = embedding_index.rankear("p", ["c"], top_n=1, encoder=encoder)

    assert not np.isnan(resultado[0][1])


# --- Degradación con gracia ------------------------------------------------


def test_modelo_no_instalado_lanza_error_tipado(monkeypatch: pytest.MonkeyPatch) -> None:
    def _falta():
        raise ImportError("No module named 'sentence_transformers'")

    monkeypatch.setattr(embedding_index, "_cargar_encoder", _falta)

    with pytest.raises(embedding_index.ModeloNoDisponibleError):
        embedding_index.rankear("p", ["c"], top_n=1)


def test_fallo_de_descarga_lanza_error_tipado(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ya pasó de verdad en este proyecto: SSL CERTIFICATE_VERIFY_FAILED. El
    RAG "local" no es local en la primera corrida."""

    def _sin_red():
        raise OSError("SSL: CERTIFICATE_VERIFY_FAILED")

    monkeypatch.setattr(embedding_index, "_cargar_encoder", _sin_red)

    with pytest.raises(embedding_index.ModeloNoDisponibleError):
        embedding_index.rankear("p", ["c"], top_n=1)


def test_el_encoder_real_pide_batch_y_sin_barra_de_progreso(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La barra de progreso ensuciaría stdout, que es por donde sale el
    resultado. Literales a propósito, no las constantes del módulo."""
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

    embedding_index.rankear("p", ["c"], top_n=1)

    assert recibido["batch_size"] == 64
    assert recibido["show_progress_bar"] is False
    assert recibido["modelo"] == "paraphrase-multilingual-MiniLM-L12-v2"


# --- Contra el modelo real -------------------------------------------------
#
# Los fragmentos son funciones completas a propósito. Una versión anterior de
# estos tests usaba recortes de dos líneas y fallaba -- pero medido, el
# problema era el fixture, no el modelo: con dos líneas las similitudes
# quedan en |s| < 0.22, o sea ruido, y AMBOS modelos se equivocan en algún
# caso. Lo que la herramienta indexa de verdad son funciones enteras, así que
# el fixture tiene que parecerse a eso. No se ablandó la aserción; se hizo
# representativa la entrada.

_FRAGMENTO_FECHAS = '''
def format_date(value, fmt="%Y-%m-%d"):
    """Formatea una fecha para mostrarla al usuario."""
    if value is None:
        return ""
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.strftime(fmt)
'''

_FRAGMENTO_REINTENTOS = '''
class HTTPAdapter(BaseAdapter):
    """Adaptador de transporte que reintenta las conexiones fallidas."""

    def __init__(self, pool_connections=10, max_retries=0):
        if max_retries == DEFAULT_RETRIES:
            self.max_retries = Retry(0, read=False)
        else:
            self.max_retries = Retry.from_int(max_retries)
        self.config = {}

    def send(self, request, timeout=None):
        try:
            resp = conn.urlopen(retries=self.max_retries, timeout=timeout)
        except (ProtocolError, OSError) as err:
            raise ConnectionError(err, request=request)
        return self.build_response(request, resp)
'''


@pytest.mark.slow
@pytest.mark.parametrize(
    "pregunta",
    [
        "where does it handle connection retries?",
        "¿dónde maneja los reintentos de conexión?",
    ],
)
def test_modelo_real_ordena_por_tema_en_los_dos_idiomas(pregunta: str) -> None:
    """La razón de usar un modelo multilingüe.

    Medido sobre psf/requests con una batería de 5 preguntas de verdad de
    campo conocida, contando si el archivo correcto entra en el top-5:

        all-MiniLM-L6-v2                        inglés 4/5   español 4/5
        paraphrase-multilingual-MiniLM-L12-v2   inglés 5/5   español 5/5
    """
    resultado = embedding_index.rankear(
        pregunta, [_FRAGMENTO_FECHAS, _FRAGMENTO_REINTENTOS], top_n=2
    )

    assert resultado[0][0] == 1

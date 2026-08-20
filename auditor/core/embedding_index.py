"""Similitud coseno sobre embeddings, para **ordenar** — nunca para juzgar.

Este módulo sobrevivió a un experimento fallido, y lo que se le sacó importa
tanto como lo que quedó.

La versión anterior exponía `cruzar(afirmaciones, notas, umbral)`, que
respondía *"¿cuál es la nota que contradice esta afirmación?"*. El ADR 0003
lo midió sobre `click`, `black` y `requests`: **18 hallazgos nuevos, los 18
falsos**, y la causa no era el umbral sino la premisa — la similitud coseno
encuentra el texto del **mismo tema**, y el diseño lo trataba como el texto
que **refuta**. Son relaciones distintas que solo coinciden por casualidad.
Esa función y su `_UMBRAL` se eliminaron: no quedan detrás de un flag,
porque un camino que ya sabemos que miente no mejora por estar apagado.

Lo que queda es `rankear()`, que responde *"¿cuáles son los N más
parecidos?"*. Es un problema de orden, no de decisión, y por eso **no tiene
umbral** — el único parámetro que había que calibrar, y que la medición
mostró que no existía, desaparece del diseño. Quien lee la lista es quien
juzga (ADR 0005).
"""

import os
from typing import Protocol

# Multilingüe a propósito, y elegido midiendo. Batería de 5 preguntas con
# verdad de campo sobre `psf/requests` (¿dónde están los reintentos? ¿la
# autenticación básica? ¿las cookies? ¿las redirecciones? ¿la subida
# multipart?), contando si el archivo correcto aparece en el top-5:
#
#   modelo                                    inglés  español  costo
#   all-MiniLM-L6-v2                            4/5      4/5   17.6s
#   paraphrase-multilingual-MiniLM-L12-v2       5/5      5/5   21.4s
#
# El modelo inglés no falla "solo en español": falla uno en cada idioma. El
# multilingüe es estrictamente mejor por 3.8s más, y este proyecto se usa en
# español — preguntar "¿dónde maneja reintentos?" y recibir código de cookies
# es exactamente el fallo silencioso que esta herramienta existe para no
# dejar pasar.
_MODELO = "paraphrase-multilingual-MiniLM-L12-v2"


class Encoder(Protocol):
    def __call__(self, textos: list[str]): ...


class ModeloNoDisponibleError(RuntimeError):
    """No se pudo cargar el modelo de embeddings.

    Se distingue de cualquier otro error para que quien llama pueda decidir
    qué hacer. Dos causas reales, las dos observadas en este proyecto: la
    librería no está instalada, o la descarga del modelo desde HuggingFace
    falla (ya pasó una vez con `SSL: CERTIFICATE_VERIFY_FAILED` — el RAG
    "local" no es local en la primera corrida).
    """


_encoder_cacheado: Encoder | None = None


def _cargar_encoder() -> Encoder:
    """Carga el modelo. Aislada en su propia función para poder sustituirla
    en los tests sin tocar red ni disco."""
    # HuggingFace escribía dos cosas por stderr antes de la primera línea
    # útil de `--ask`: un warning de que la petición va sin token y una barra
    # de progreso de carga de pesos. Medido cuál apaga cuál, en subprocesos
    # separados: la barra la apaga `HF_HUB_DISABLE_PROGRESS_BARS`, el warning
    # `HF_HUB_VERBOSITY`, y ni `TRANSFORMERS_VERBOSITY` ni
    # `HF_HUB_DISABLE_TELEMETRY` apagan ninguno de los dos.
    #
    # Va antes del import y con `setdefault` a propósito: las variables se
    # leen cuando `huggingface_hub` se importa, y quien las haya puesto a
    # mano —para depurar una descarga que falla— gana sobre nosotros.
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("HF_HUB_VERBOSITY", "error")

    from sentence_transformers import SentenceTransformer

    modelo = SentenceTransformer(_MODELO)

    def encode(textos: list[str]):
        return modelo.encode(textos, batch_size=64, show_progress_bar=False)

    return encode


def _encoder() -> Encoder:
    """El modelo se carga una sola vez por proceso: pagar el costo fijo dos
    veces en la misma corrida sería tirar el beneficio de la carga perezosa."""
    global _encoder_cacheado
    if _encoder_cacheado is None:
        try:
            _encoder_cacheado = _cargar_encoder()
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            raise ModeloNoDisponibleError(
                f"no se pudo cargar el modelo de embeddings '{_MODELO}': {exc}"
            ) from exc
    return _encoder_cacheado


def _normalizar(vectores):
    """Deja el producto punto igual a la similitud coseno.

    Sin esto el producto punto premia al vector más LARGO en vez de al más
    parecido en dirección, y el modelo real devuelve normas arbitrarias. El
    `1e-12` evita dividir por cero ante un vector nulo, que no debería
    ocurrir pero costaría un NaN silencioso propagado al orden.
    """
    import numpy as np

    return vectores / (np.linalg.norm(vectores, axis=1, keepdims=True) + 1e-12)


def rankear(
    consulta: str,
    candidatos: list[str],
    top_n: int,
    encoder: Encoder | None = None,
) -> list[tuple[int, float]]:
    """Los `top_n` candidatos más parecidos a `consulta`, de mayor a menor.

    Devuelve `(índice, similitud)`. La similitud va cruda a propósito: es una
    distancia coseno, no una probabilidad de que la respuesta sea correcta, y
    disfrazarla de porcentaje invitaría a leerla como lo segundo.

    Con `candidatos` vacío no carga el modelo y no lanza.
    """
    if not candidatos:
        return []

    encode = encoder if encoder is not None else _encoder()

    import numpy as np

    vec_consulta = _normalizar(np.asarray(encode([consulta]), dtype="float32"))
    vec_candidatos = _normalizar(np.asarray(encode(candidatos), dtype="float32"))

    similitudes = (vec_candidatos @ vec_consulta.T).ravel()
    orden = np.argsort(-similitudes)[:top_n]
    return [(int(i), float(similitudes[i])) for i in orden]

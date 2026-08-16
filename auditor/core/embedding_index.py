"""Cruce semántico afirmación↔evidencia (ADR 0003, Paso 1).

Reemplaza la intersección de palabras clave de `semantic_check.py` por
similitud coseno entre embeddings, para atacar una limitación que el ADR
0002 ya tenía documentada como conocida:

    "El cruce de keywords es cross-language ciego. Las notas de evidencia
    están en español; un README en inglés ('100% test coverage') puede no
    compartir ningún token con una nota como 'README afirma... pero no hay
    funciones de test'."

Dos decisiones de diseño que vienen de haber medido antes de escribir código
(ADR 0003, "Costo real, medido"):

1. **Carga perezosa.** El modelo cuesta ~7.6s fijos. Este módulo no lo toca
   hasta tener afirmaciones Y notas que cruzar; con cualquiera de las dos
   listas vacía devuelve sin cargar nada. Es el mismo criterio con el que el
   agente de triage no gastó una sola llamada de API en `pallets/click`.

2. **El corpus es chico a propósito.** Solo se embeben las afirmaciones
   extraídas del README y las notas de evidencia ya producidas — del orden de
   30 strings, 0.04s medidos. Embeber el código del repo entero cuesta entre
   4 y 25 segundos más y es el Paso 2, condicional a que este paso demuestre
   servir para algo.

Este módulo **no decide veredictos**: devuelve qué nota se parece más a cada
afirmación, y quien llama decide qué hacer con eso. El veredicto sigue
saliendo de código determinista en `semantic_check.py`.
"""

from typing import Protocol

_MODELO = "all-MiniLM-L6-v2"

# Umbral de similitud coseno para considerar que una nota habla del mismo
# tema que una afirmación.
#
# Calibrado midiendo 11 pares reales (afirmaciones típicas de README contra
# las notas que producen los 4 verificadores) con el modelo real, no elegido
# a ojo. Los resultados por umbral:
#
#   0.25 -> encuentra 1 que keywords pierde, pero mete 1 falso positivo
#   0.30 -> encuentra 1 que keywords pierde, 0 falsos positivos
#   0.35 -> ya no encuentra nada que keywords no encuentre
#
# 0.30 es el único que suma sin costo. Por encima, el aporte desaparece; por
# debajo, empieza a marcar afirmaciones sin relación real ("Follows PEP 8
# style guidelines" contra una nota de vulnerabilidades).
#
# Honestidad sobre la calibración: son 11 casos construidos a mano, y el
# "esperado" de cada uno lo decidí yo. No hay ground truth verificable acá
# como sí lo había con los secretos, donde se abría el archivo y se veía.
# Está anotado en el ADR 0003 como límite conocido de este paso.
_UMBRAL = 0.30


class Encoder(Protocol):
    def __call__(self, textos: list[str]): ...


class ModeloNoDisponibleError(RuntimeError):
    """No se pudo cargar el modelo de embeddings.

    Se distingue de cualquier otro error para que quien llama pueda caer al
    cruce de keywords y dejar constancia, en vez de tumbar el pipeline. Dos
    causas reales, las dos observadas en este proyecto: la librería no está
    instalada, o la descarga del modelo desde HuggingFace falla (ya pasó una
    vez con `SSL: CERTIFICATE_VERIFY_FAILED` — el RAG "local" no es local en
    la primera corrida).
    """


_encoder_cacheado: Encoder | None = None


def _cargar_encoder() -> Encoder:
    """Carga el modelo. Aislada en su propia función para poder sustituirla
    en los tests sin tocar red ni disco."""
    from sentence_transformers import SentenceTransformer

    modelo = SentenceTransformer(_MODELO)

    def encode(textos: list[str]):
        return modelo.encode(textos, batch_size=64, show_progress_bar=False)

    return encode


def _encoder() -> Encoder:
    """El modelo se carga una sola vez por proceso: pagar 7.6s dos veces en
    la misma corrida sería tirar el beneficio de la carga perezosa."""
    global _encoder_cacheado
    if _encoder_cacheado is None:
        try:
            _encoder_cacheado = _cargar_encoder()
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            raise ModeloNoDisponibleError(
                f"no se pudo cargar el modelo de embeddings '{_MODELO}': {exc}"
            ) from exc
    return _encoder_cacheado


def cruzar(
    afirmaciones: list[str],
    notas: list[str],
    umbral: float = _UMBRAL,
    encoder: Encoder | None = None,
) -> list[int | None]:
    """Para cada afirmación, el índice de la nota más parecida, o None.

    "Más parecida" es la de mayor similitud coseno, siempre que supere
    `umbral`. Se devuelve la mejor y no la primera que pase: con varias notas
    relacionadas, citar la más cercana es lo que hace útil la evidencia.

    Lanza `ModeloNoDisponibleError` si hay algo que cruzar y el modelo no
    carga. Con listas vacías no carga nada y no lanza.
    """
    if not afirmaciones:
        return []
    if not notas:
        return [None] * len(afirmaciones)

    encode = encoder if encoder is not None else _encoder()

    import numpy as np

    vec_afirmaciones = np.asarray(encode(afirmaciones), dtype="float32")
    vec_notas = np.asarray(encode(notas), dtype="float32")

    # Normalizar deja el producto punto igual a la similitud coseno. El
    # `1e-12` evita dividir por cero ante un vector nulo, que no deberia
    # ocurrir pero costaria un NaN silencioso propagado al umbral.
    vec_afirmaciones /= np.linalg.norm(vec_afirmaciones, axis=1, keepdims=True) + 1e-12
    vec_notas /= np.linalg.norm(vec_notas, axis=1, keepdims=True) + 1e-12

    similitudes = vec_afirmaciones @ vec_notas.T

    resultado: list[int | None] = []
    for fila in similitudes:
        mejor = int(fila.argmax())
        resultado.append(mejor if float(fila[mejor]) >= umbral else None)
    return resultado

"""`--ask`: recuperación exploratoria sobre el código, sin veredicto.

Devuelve los fragmentos de código más relacionados con una pregunta en
lenguaje natural, cada uno con su `archivo:línea`. **No decide nada** — no
emite `Verdict`, no entra al reporte de auditoría, no participa de ningún
`worst_verdict`.

Es la única forma de recuperación que sobrevivió a la medición del ADR 0003,
y sobrevive precisamente porque no afirma nada. Aquel experimento convertía
"esto es lo más parecido" en "esto contradice la afirmación", un salto que
los números desmintieron: 18 hallazgos nuevos sobre repos reales, los 18
falsos. Acá el salto no se da. La herramienta entrega la lista y ahí termina;
quien pregunta es quien juzga, y sabe que preguntó.

No hay LLM en este camino: embeddings, producto punto y un `argsort`.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from auditor.core import code_chunks, embedding_index
from auditor.core.code_chunks import Fragmento
from auditor.core.embedding_index import Encoder

_TOP_N = 5

# Se repite igual en el texto, en el JSON y en el README. No es decorativa:
# es la diferencia entre esta herramienta y el diseño que el ADR 0003
# descartó. Si quien lee toma la lista por un veredicto, volvimos al error
# que se midió.
ADVERTENCIA = "esto te muestra dónde mirar, no te dice si algo es cierto"


@dataclass(frozen=True)
class Respuesta:
    pregunta: str
    fragmentos: tuple[tuple[Fragmento, float], ...]
    archivos_leidos: int
    truncado: bool


def buscar(
    root: Path,
    pregunta: str,
    top_n: int = _TOP_N,
    max_fragmentos: int = code_chunks._MAX_FRAGMENTOS,
    encoder: Encoder | None = None,
) -> Respuesta:
    """Trocea el repo, embebe pregunta y fragmentos, y ordena por similitud.

    Con un repo sin código indexable no se toca el modelo: `rankear` devuelve
    `[]` sin cargar nada, así que los 7.6s fijos no se pagan. Mismo criterio
    con el que el agente de triage no gastó una llamada de API en
    `pallets/click`.
    """
    corpus = code_chunks.extraer(root, max_fragmentos=max_fragmentos)
    ranking = embedding_index.rankear(
        pregunta, [f.texto for f in corpus.fragmentos], top_n=top_n, encoder=encoder
    )
    return Respuesta(
        pregunta=pregunta,
        fragmentos=tuple(
            (corpus.fragmentos[indice], similitud) for indice, similitud in ranking
        ),
        archivos_leidos=corpus.archivos_leidos,
        truncado=corpus.truncado,
    )


def _nota_de_truncado(respuesta: Respuesta) -> str:
    return (
        f"corpus truncado en {len(respuesta.fragmentos)} fragmentos: que algo no "
        "aparezca acá no significa que no esté en el repo"
    )


def to_texto(respuesta: Respuesta) -> str:
    lineas = [f'Pregunta: "{respuesta.pregunta}"', f"({ADVERTENCIA})", ""]

    if not respuesta.fragmentos:
        lineas.append(
            "no se encontró código indexable en el repo — nada que buscar"
            if respuesta.archivos_leidos == 0
            else "ningún fragmento para ordenar"
        )
        return "\n".join(lineas)

    lineas.append(f"{respuesta.archivos_leidos} archivos indexados. Más relacionados:")
    lineas.append("")
    for fragmento, similitud in respuesta.fragmentos:
        # La similitud va cruda: es una distancia coseno, no una probabilidad
        # de que la respuesta sea correcta.
        lineas.append(
            f"  {fragmento.file}:{fragmento.line}  (similitud {similitud:.3f})"
        )
        lineas.append(f"      {fragmento.firma}")
    if respuesta.truncado:
        lineas.extend(["", _nota_de_truncado(respuesta)])
    return "\n".join(lineas)


def to_json(respuesta: Respuesta) -> str:
    datos = {
        "pregunta": respuesta.pregunta,
        "advertencia": ADVERTENCIA,
        "archivos_indexados": respuesta.archivos_leidos,
        "truncado": respuesta.truncado,
        "fragmentos": [
            {
                "file": fragmento.file,
                "line": fragmento.line,
                "firma": fragmento.firma,
                "similitud": round(similitud, 6),
            }
            for fragmento, similitud in respuesta.fragmentos
        ],
    }
    return json.dumps(datos, indent=2, ensure_ascii=False)

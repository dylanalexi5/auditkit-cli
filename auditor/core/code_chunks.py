"""Trocea el código del repo en fragmentos indexables (ADR 0005, `--ask`).

Un fragmento es una función, un método o una clase, con su `archivo:línea` y
su texto fuente. Es lo que `--ask` embebe para ordenar por similitud contra
una pregunta en lenguaje natural.

**Por qué no reusa `symbol_index.py`.** Ese módulo responde "¿qué nombres
exporta este paquete?" y por eso mira solo definiciones de nivel superior:
`click.echo` significa un nombre exportado, no cualquier método enterrado en
una clase (ADR 0004). Acá la pregunta es otra —"¿dónde está el código que
habla de esto?"— y la respuesta a "¿dónde maneja reintentos de conexión?"
suele vivir justamente en un método (`HTTPAdapter.send`). Son dos preguntas
distintas sobre el mismo árbol, con criterios de inclusión opuestos, así que
son dos módulos.

Este módulo no decide nada: devuelve fragmentos. Quien pregunta es quien
juzga si la respuesta está entre ellos.
"""

import ast
from dataclasses import dataclass
from pathlib import Path

from auditor.core.repo_context import is_test_fixture_path

# Tope de fragmentos. `psf/black` mide 3033 y tarda 25.5s en embeberse
# (ADR 0003); un monorepo sin tope cuelga la corrida. Cuando se corta hay que
# decirlo: con el corpus truncado, "no aparece en los resultados" deja de
# significar "no está en el repo".
_MAX_FRAGMENTOS = 3000

# El modelo trunca a 256 tokens de todas formas. Mandarle 40 KB de una sola
# función es gasto puro, y el recorte mantiene acotado lo que se muestra.
_MAX_CARACTERES = 1500


@dataclass(frozen=True)
class Fragmento:
    file: str
    line: int
    firma: str
    texto: str


@dataclass(frozen=True)
class Corpus:
    fragmentos: tuple[Fragmento, ...]
    archivos_leidos: int
    truncado: bool


def _firma(lineas: list[str], nodo: ast.AST) -> str:
    """La primera línea de la definición, sin el cuerpo.

    Va al reporte para que el lector ubique el fragmento sin leer las 40
    líneas que siguen.
    """
    return lineas[nodo.lineno - 1].strip()


def _texto(lineas: list[str], nodo: ast.AST) -> str:
    fin = getattr(nodo, "end_lineno", None) or nodo.lineno
    return "\n".join(lineas[nodo.lineno - 1 : fin])[:_MAX_CARACTERES]


def extraer(root: Path, max_fragmentos: int = _MAX_FRAGMENTOS) -> Corpus:
    """Recorre el repo y devuelve los fragmentos indexables.

    Un archivo que no parsea se saltea en silencio, mismo criterio que
    `deps_check` y `symbol_index`: `psf/black` tiene `.py` inválidos a
    propósito como input de tests.
    """
    fragmentos: list[Fragmento] = []
    archivos = 0
    truncado = False

    for py_file in sorted(root.rglob("*.py")):
        # `rglob("*.py")` también devuelve DIRECTORIOS terminados en `.py`;
        # leerlos revienta con PermissionError en Windows e IsADirectoryError
        # en POSIX. Mismo bug que apareció en `symbol_index` (ADR 0004).
        if not py_file.is_file():
            continue
        partes = py_file.relative_to(root).parts
        if is_test_fixture_path(partes):
            continue

        try:
            fuente = py_file.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(fuente)
        except (SyntaxError, ValueError, OSError):
            continue

        archivos += 1
        relativo = "/".join(partes)
        lineas = fuente.splitlines()

        for nodo in ast.walk(tree):
            if not isinstance(
                nodo, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
            ):
                continue
            if len(fragmentos) >= max_fragmentos:
                truncado = True
                break
            fragmentos.append(
                Fragmento(
                    file=relativo,
                    line=nodo.lineno,
                    firma=_firma(lineas, nodo),
                    texto=_texto(lineas, nodo),
                )
            )
        if truncado:
            break

    return Corpus(
        fragmentos=tuple(fragmentos), archivos_leidos=archivos, truncado=truncado
    )

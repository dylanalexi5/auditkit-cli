"""API pública por paquete: `paquete → {nombre → [(archivo, línea)]}`.

Es la pieza que el ADR 0001 prometía y nunca se implementó. Su tabla de
librerías decía, sobre `readme_check.py`:

    "`ast` extrae funciones/clases/entrypoints reales del código (verdad)"

En los hechos `readme_check.py` solo contaba funciones `test_*` para
contrastar una afirmación de coverage. La "verdad del código" contra la que
se comparaba el README era un único booleano: *¿hay algún test?*

**Por qué está indexado por paquete y no como una lista plana de nombres.**
La primera versión de este módulo devolvía un único diccionario con todos los
nombres del repo. Un control positivo lo tumbó: se renombró `def echo(` en
`pallets/click` —la función que su propio README usa como ejemplo— y el
verificador siguió diciendo que `click.echo` existía. Resolvía contra
`tests/test_utils/test_prompt.py:94`, una función de test que se llamaba
igual. Con la tabla plana, `click.command` también "existía" por un método
enterrado en una clase de `core.py`, no por el decorador real de
`decorators.py`.

O sea que un repo grande satisface casi cualquier nombre por accidente. La
tabla plana no medía "esto es parte de la API del proyecto" sino "esta
cadena aparece en algún lado", que no es lo que un README afirma cuando
escribe `click.echo(...)`.

De ahí las dos reglas:

1. **Resolución por paquete raíz.** `click.echo` se busca en el espacio de
   `click`, no en el del repo. Los archivos de `tests/` caen bajo la raíz
   `tests` y por lo tanto no pueden satisfacer una cita a `click`.
2. **Solo definiciones de nivel superior** (`tree.body`, no `ast.walk`).
   `click.echo` significa un nombre exportado por el paquete, y eso es un
   `def`/`class`/asignación de módulo — no un método adentro de una clase.
   Las reexportaciones (`from .utils import echo` en `__init__.py`) quedan
   cubiertas igual, porque `echo` sí es de nivel superior en `utils.py` y
   `utils.py` pertenece al paquete `click`.

Vive en `core/` y no en `verifiers/` porque no es un verificador: no emite
veredicto ni evidencia. Es una primitiva, y quien la use decide qué significa
que un nombre esté o no esté.
"""

import ast
from dataclasses import dataclass
from pathlib import Path

from auditor.core.repo_context import is_test_fixture_path

# Tope duro de archivos parseados. `readme_check` corre por defecto sobre
# repos ajenos y no controlamos su tamaño; sin tope, un monorepo cuelga la
# corrida entera. 2000 entra holgado en los repos medidos (psf/black, el más
# grande de los tres, indexa 75 archivos).
_MAX_ARCHIVOS = 2000

Ubicacion = tuple[str, int]


@dataclass(frozen=True)
class Indice:
    """`truncado` no es un detalle interno: si se cortó por el tope, la
    ausencia de un símbolo deja de significar "no existe" y pasa a significar
    "no lo vimos". Quien consulta la tabla necesita poder distinguirlos."""

    publicos: dict[str, dict[str, list[Ubicacion]]]
    archivos_leidos: tuple[str, ...]
    truncado: bool

    @property
    def paquetes(self) -> frozenset[str]:
        """Espacio de nombres importable del repo."""
        return frozenset(self.publicos)

    def resuelve(self, paquete: str, nombre: str) -> list[Ubicacion]:
        return self.publicos.get(paquete, {}).get(nombre, [])


def _definiciones_de_nivel_superior(tree: ast.Module) -> list[tuple[str, int]]:
    """Lo que el módulo exporta: `def`, `class` y asignaciones de módulo.

    Se recorre `tree.body` y no `ast.walk` a propósito — ver el docstring del
    módulo. Las asignaciones cuentan porque `requests.codes` es una (en
    `status_codes.py`), y sin ellas una cita legítima se reportaría como
    inexistente.
    """
    nombres: list[tuple[str, int]] = []
    for nodo in tree.body:
        if isinstance(nodo, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            nombres.append((nodo.name, nodo.lineno))
        elif isinstance(nodo, ast.Assign):
            nombres.extend(
                (objetivo.id, objetivo.lineno)
                for objetivo in nodo.targets
                if isinstance(objetivo, ast.Name)
            )
        elif isinstance(nodo, ast.AnnAssign) and isinstance(nodo.target, ast.Name):
            nombres.append((nodo.target.id, nodo.lineno))
    return nombres


def _paquete_raiz(partes: tuple[str, ...]) -> str | None:
    """Nombre importable de nivel superior al que pertenece un archivo.

    `src/` no es un paquete: es el layout que usan `psf/black` y
    `psf/requests`, y el nombre importable es lo que hay adentro. Un archivo
    suelto en la raíz (`setup.py`) es importable por su propio nombre.
    """
    if partes and partes[0] == "src":
        partes = partes[1:]
    if not partes:
        return None
    if len(partes) == 1:
        return partes[0].removesuffix(".py") or None
    return partes[0]


def construir(root: Path, max_archivos: int = _MAX_ARCHIVOS) -> Indice:
    """Parsea el repo una vez y devuelve la tabla.

    Un archivo que no parsea se saltea en silencio, mismo criterio que
    `deps_check`: `psf/black` tiene `.py` inválidos **a propósito** como input
    de tests, y un `SyntaxError` ajeno no puede tumbar un verificador que
    corre por defecto.
    """
    publicos: dict[str, dict[str, list[Ubicacion]]] = {}
    leidos: list[str] = []
    truncado = False

    for py_file in sorted(root.rglob("*.py")):
        partes = py_file.relative_to(root).parts
        if is_test_fixture_path(partes):
            continue
        raiz = _paquete_raiz(partes)
        if raiz is None:
            continue
        if len(leidos) >= max_archivos:
            truncado = True
            break

        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8", errors="ignore"))
        except (SyntaxError, ValueError):
            continue

        relativo = "/".join(partes)
        leidos.append(relativo)
        espacio = publicos.setdefault(raiz, {})

        # El submódulo es un nombre citable: `requests.exceptions.HTTPError`
        # nombra `exceptions`, que es un archivo, no una definición.
        if py_file.stem != "__init__":
            espacio.setdefault(py_file.stem, []).append((relativo, 1))

        for nombre, linea in _definiciones_de_nivel_superior(tree):
            espacio.setdefault(nombre, []).append((relativo, linea))

    return Indice(
        publicos=publicos, archivos_leidos=tuple(leidos), truncado=truncado
    )

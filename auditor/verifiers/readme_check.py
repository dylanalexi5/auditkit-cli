"""Contrasta lo que el README afirma contra lo que el código realmente tiene.

Dos chequeos, los dos deterministas y los dos con `archivo:línea` real:

1. **Coverage** (`N% coverage`) contra la existencia de funciones `test_*`.
2. **Identificadores citados en ejemplos de uso** contra la tabla de símbolos
   que `ast` extrae del repo (`auditor/core/symbol_index.py`).

El segundo es el que el ADR 0001 prometía en su tabla de librerías —
*"`ast` extrae funciones/clases/entrypoints reales del código"*— y que hasta
ahora no existía: la única "verdad del código" contra la que se comparaba el
README era un booleano, *¿hay algún test?*.
"""

import ast
import re
from pathlib import Path

from auditor.core import symbol_index
from auditor.core.models import Evidence, Verdict, VerifierResult, worst_verdict
from auditor.core.repo_context import RepoContext, declared_project_names

_COVERAGE_CLAIM = re.compile(r"\d{1,3}%\s*(?:test\s*)?coverage", re.IGNORECASE)
_README_NAMES = ("README.md", "README.rst", "README.txt", "README")

# Solo se miran fragmentos que el README marca COMO CÓDIGO: bloques con
# fence y spans entre backticks. La prosa queda afuera a propósito — una
# oración como "antes esto se llamaba demo.viejo_nombre" no es un ejemplo de
# uso, y tratarla como tal solo agrega falsos positivos.
#
# Del fence se exige la etiqueta de lenguaje Python explícita. Medido contra
# `psf/requests`: su README tiene un bloque ```shell con
# `git clone https://github.com/psf/requests.git`, que salía reportado como
# "el ejemplo cita 'git' pero no existe". Un fence sin etiqueta puede ser
# shell, texto o salida de consola, así que tampoco se revisa: un falso "esto
# no existe" es una acusación, una revisión de menos es solo un hueco.
_FENCE = re.compile(r"```.*?```", re.DOTALL)
_FENCE_PYTHON = re.compile(
    r"```(?:python|py|python3|pycon)\b(.*?)```", re.DOTALL | re.IGNORECASE
)
_INLINE = re.compile(r"``([^`]+)``|`([^`\n]+)`")
# En RST el backtick SIMPLE no delimita código: es una referencia, y el uso
# de lejos más común es un link — `texto <url>`_. El literal se escribe con
# backtick doble. Aplicarle la regla de markdown metía el texto visible de
# cada link adentro de un "ejemplo de uso". Medido en `arrow-py/arrow`,
# README.rst:116: `arrow.readthedocs.io <https://arrow.readthedocs.io>`_
# salía reportado como "'readthedocs' no está definido en el paquete
# 'arrow'". El backtick doble se sigue revisando, así que el chequeo no se
# apaga en los repos .rst — solo deja de leer prosa enlazada como código.
_INLINE_RST = re.compile(r"``([^`]+)``")
_IMPORT_FROM = re.compile(r"\bfrom\s+([\w.]+)\s+import\s+([^\n#]+)")
_ATRIBUTO = re.compile(r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)")
_NOMBRE = re.compile(r"[A-Za-z_]\w*")
# `https://github.com/psf/requests.git` contiene `requests.git`, que matchea
# `_ATRIBUTO` igual que un atributo real. Lo que lo descalifica es el
# carácter pegado al nombre: un separador de ruta antes, o una URL que sigue
# después. Mirar el token entero era demasiado grosero —
# `requests.get('https://httpbin.org/x')` también tiene barras, pero en el
# ARGUMENTO, y descartarlo dejaba a `psf/requests` sin una sola cita
# revisada.
_SEPARADOR_DE_RUTA = "/\\."
_SIGUE_LA_URL = "/:"


def _find_readme(path: Path) -> Path | None:
    for name in _README_NAMES:
        candidate = path / name
        if candidate.is_file():
            return candidate
    return None


def _count_test_functions(path: Path) -> int:
    count = 0
    for test_file in path.rglob("test_*.py"):
        tree = ast.parse(test_file.read_text(encoding="utf-8", errors="ignore"))
        count += sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
        )
    return count


def _linea_de(texto: str, offset: int) -> int:
    return texto.count("\n", 0, offset) + 1


def _sin_fences(readme_text: str) -> str:
    """El mismo texto con los bloques con fence tapados por espacios.

    Hace falta antes de buscar backticks sueltos: los ``` de un fence son
    backticks, así que el regex de span inline los agarra y termina leyendo
    el contenido de un bloque shell como si fuera una cita en prosa. Se tapa
    con espacios y no se borra para que los offsets —y por lo tanto los
    números de línea— sigan siendo los del README original.
    """
    return _FENCE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), readme_text)


def _inline_de(readme_path: Path) -> re.Pattern[str]:
    return _INLINE_RST if readme_path.suffix.lower() == ".rst" else _INLINE


def _fragmentos_de_codigo(readme_text: str, inline: re.Pattern[str] = _INLINE):
    """Bloques con fence de Python explícito, más spans entre backticks."""
    yield from _FENCE_PYTHON.finditer(readme_text)
    yield from inline.finditer(_sin_fences(readme_text))


def _vive_dentro_de_una_ruta(fragmento: str, inicio: int, fin: int) -> bool:
    """Si el `a.b` que matcheó es en realidad un pedazo de URL o de ruta.

    Dos señales, y el alcance de cada una importa:

    - Un separador de ruta **justo antes**: `.../psf/requests.git`.
    - Una barra o dos puntos al final de la **cadena punteada** que empieza
      en el match: `demo.example.com/api`. No alcanza con mirar el carácter
      pegado, porque ahí hay un punto — hay que recorrer hasta donde termina
      la cadena de identificadores. Y tampoco se puede mirar el token entero
      separado por espacios: `demo.get('https://httpbin.org/x')` tiene barras
      en el ARGUMENTO, y mirar tan lejos dejó a `psf/requests` sin una sola
      cita revisada.
    """
    anterior = fragmento[inicio - 1] if inicio > 0 else ""
    if anterior and anterior in _SEPARADOR_DE_RUTA:
        return True

    limite = fin
    while limite < len(fragmento) and (
        fragmento[limite].isalnum() or fragmento[limite] in "._"
    ):
        limite += 1
    siguiente = fragmento[limite] if limite < len(fragmento) else ""
    return bool(siguiente) and siguiente in _SIGUE_LA_URL


def _citas_del_proyecto(
    readme_text: str, espacio_propio: frozenset[str], inline: re.Pattern[str] = _INLINE
) -> list[tuple[str, str, int]]:
    """Identificadores que el README le atribuye a ESTE repo, con su línea.

    El filtro por `espacio_propio` es lo que hace usable el chequeo. Un README
    normal está lleno de código que no es del proyecto — `os.path.join`,
    `pd.DataFrame()`, el `requests.get()` de un ejemplo de integración — y
    reportar esos como "no existe" sería una acusación falsa sobre código que
    ni siquiera vive acá. Solo se revisa lo que el propio README presenta
    como parte del proyecto: `from <proyecto> import X` o `<proyecto>.X`.
    """
    citas: list[tuple[str, str, int]] = []
    for bloque in _fragmentos_de_codigo(readme_text, inline):
        fragmento = bloque.group(0)
        base = bloque.start()

        for m in _IMPORT_FROM.finditer(fragmento):
            modulo, importados = m.group(1), m.group(2)
            paquete = modulo.split(".")[0]
            if paquete not in espacio_propio:
                continue
            linea = _linea_de(readme_text, base + m.start())
            for parte in importados.split(","):
                nombre = _NOMBRE.search(parte)
                # `as` renombra: de `from x import a as b` lo verificable es
                # `a`, que es lo que el regex agarra primero.
                if nombre is not None:
                    citas.append((paquete, nombre.group(0), linea))

        for m in _ATRIBUTO.finditer(fragmento):
            if m.group(1) not in espacio_propio:
                continue
            if _vive_dentro_de_una_ruta(fragmento, m.start(), m.end()):
                continue
            citas.append(
                (m.group(1), m.group(2), _linea_de(readme_text, base + m.start()))
            )

    return citas


def _revisar_identificadores(
    ctx: RepoContext, readme_path: Path, readme_text: str
) -> list[Evidence]:
    # Se mira primero si hay algún fragmento de código. Sin esto, todo repo
    # con README pagaría el parseo completo del árbol aunque su README no
    # traiga un solo ejemplo de uso.
    inline = _inline_de(readme_path)
    if next(_fragmentos_de_codigo(readme_text, inline), None) is None:
        return []

    indice = symbol_index.construir(ctx.path)
    # El nombre declarado en pyproject cuenta aunque no coincida con ningún
    # directorio (un paquete puede publicarse con un nombre y vivir en otro),
    # pero la resolución siempre va contra un paquete que exista en el árbol.
    espacio_propio = frozenset(
        set(declared_project_names(ctx.path)) | set(indice.paquetes)
    )
    citas = _citas_del_proyecto(readme_text, espacio_propio, inline)
    faltantes = [(p, n, ln) for p, n, ln in citas if not indice.resuelve(p, n)]
    if not faltantes:
        return []

    if indice.truncado:
        # Con el índice cortado, "no está en la tabla" ya no significa "no
        # existe" sino "no lo miramos". Reportarlo como ausencia sería
        # inventar un hallazgo.
        return [
            Evidence(
                file="(sin verificar)",
                line=0,
                note=(
                    f"el repo supera el tope de {len(indice.archivos_leidos)} archivos "
                    "indexados: no se verificaron los identificadores del README"
                ),
            )
        ]

    vistos: set[tuple[str, str, int]] = set()
    evidencia: list[Evidence] = []
    for paquete, nombre, linea in faltantes:
        if (paquete, nombre, linea) in vistos:
            continue
        vistos.add((paquete, nombre, linea))
        evidencia.append(
            Evidence(
                file=readme_path.name,
                line=linea,
                note=(
                    f"el ejemplo de uso del README cita '{paquete}.{nombre}' pero "
                    f"'{nombre}' no está definido en el paquete '{paquete}'"
                ),
            )
        )
    return evidencia


def verify(ctx: RepoContext) -> VerifierResult:
    readme_path = _find_readme(ctx.path)
    if readme_path is None:
        return VerifierResult(verdict=Verdict.APROBADO, evidence=[])

    readme_text = readme_path.read_text(encoding="utf-8", errors="ignore")

    claims = [
        (line_number, match.group(0))
        for line_number, line in enumerate(readme_text.splitlines(), start=1)
        for match in [_COVERAGE_CLAIM.search(line)]
        if match
    ]
    coverage_rota = bool(claims) and _count_test_functions(ctx.path) == 0
    evidencia = [
        Evidence(
            file=readme_path.name,
            line=line_number,
            note=f'README afirma "{claim}" pero no hay funciones de test en el repo',
        )
        for line_number, claim in claims
        if coverage_rota
    ]

    identificadores = _revisar_identificadores(ctx, readme_path, readme_text)
    evidencia.extend(identificadores)

    # Techo deliberado para el chequeo de identificadores: solo
    # APROBADO_CON_OBSERVACIONES. "No hay una sola función de test" no admite
    # lectura alternativa y por eso el de coverage sí llega a NO_SOSTENIBLE;
    # un identificador ausente sí la admite —se genera dinámicamente, es API
    # planeada, viene de una extensión C— así que hasta medirlo sobre repos
    # reales se queda en observación.
    veredictos = [Verdict.NO_SOSTENIBLE] if coverage_rota else []
    if identificadores:
        veredictos.append(Verdict.APROBADO_CON_OBSERVACIONES)
    return VerifierResult(verdict=worst_verdict(veredictos), evidence=evidencia)

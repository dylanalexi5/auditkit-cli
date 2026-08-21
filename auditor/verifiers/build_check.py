import os
import re
import subprocess
import sys
from pathlib import Path

from auditor.core.models import Evidence, Verdict, VerifierResult
from auditor.core.repo_context import (
    RepoContext,
    declarado_como,
    normalize_dependency_name,
)

_TIMEOUT_SECONDS = 300
_PROJECT_MARKERS = ("pyproject.toml", "setup.py", "setup.cfg")
_SUMMARY_LINE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+?\.py)(?:::\S+)?(?:\s+-\s+(.*))?$")
_ERROR_DETAIL_LINE = re.compile(r"^E\s+(.+)$")
_MODULE_NOT_FOUND = re.compile(r"ModuleNotFoundError: No module named '([^']+)'")
_UNDECLARED_DEP_NOTE = "dependencia declarada no instalada - no verificado, no es un fallo real"
# La linea del frame que fallo: `tests\test_x.py:7: AssertionError`. Es lo
# unico de la salida de pytest que ubica el fallo — el resumen (`FAILED
# tests/test_x.py::test_y`) nombra el archivo pero nunca la linea.
_LOCATION_LINE = re.compile(r"^(\S+?\.py):(\d+): ")
_SIN_UBICACION = "ubicacion no determinada en la salida de pytest"
# Sin esto, cada pytest que lanza `verify()` puede abrir su propia consola en
# blanco cuando el proceso padre no tiene una consola propia adjunta -- un
# worker corriendo en background, por ejemplo. Con una sola llamada no se
# nota; con mutation testing real (cientos de llamadas) esto colgo la
# maquina de verdad, dos veces. CREATE_NO_WINDOW solo existe en Windows; el
# `if` evita tocar el atributo en otros sistemas, donde no existe.
_CREATIONFLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _is_python_project(path: Path) -> bool:
    return any((path / marker).is_file() for marker in _PROJECT_MARKERS)


def _pytest_env(path: Path) -> dict[str, str]:
    """Hace importable el paquete propio del repo agregando la raiz y src/ al
    PYTHONPATH. Sin esto, cualquier repo con layout src/ falla con
    ModuleNotFoundError de su propio paquete y se reporta como roto sin estarlo.

    Deliberadamente NO se usa `pip install -e .`: instalar el repo ejecuta su
    build backend (setup.py / pyproject.toml), que es codigo del repo auditado,
    y ademas resuelve y descarga sus dependencias externas. Ambas cosas estan
    fuera del riesgo aceptado (ver ADR). Extender PYTHONPATH consigue lo mismo
    para el paquete propio sin ejecutar nada extra ni tocar la red."""
    entries = [str(path)]
    src_dir = path / "src"
    if src_dir.is_dir():
        entries.append(str(src_dir))

    existing = os.environ.get("PYTHONPATH")
    if existing:
        entries.append(existing)

    return {**os.environ, "PYTHONPATH": os.pathsep.join(entries)}


def _ubicaciones(lines: list[str]) -> list[tuple[str, int]]:
    """`(archivo, linea)` de cada frame que pytest ubica, en orden de salida.

    Se normalizan las barras porque pytest imprime el traceback con el
    separador del sistema (`tests\\test_x.py` en Windows) y el resumen
    siempre con `/`.
    """
    return [
        (match.group(1).replace("\\", "/"), int(match.group(2)))
        for match in map(_LOCATION_LINE.match, lines)
        if match
    ]


def _tomar_linea(ubicaciones: list[tuple[str, int]], archivo: str) -> int | None:
    """La primera ubicacion pendiente de ese archivo, y la consume.

    Consumirla importa: dos fallos en el mismo archivo estan en dos lineas
    distintas, y pytest imprime las secciones de fallo y las del resumen en
    el mismo orden. Sin consumir, el segundo fallo heredaria la linea del
    primero — que es fabricar una ubicacion con otra cara.

    None cuando no hay ninguna: pasa de verdad, por ejemplo cuando el fallo
    ocurre dentro de un fixture de `conftest.py` y la unica linea que pytest
    imprime es la del conftest, no la del archivo que nombra el resumen.
    """
    for indice, (ruta, linea) in enumerate(ubicaciones):
        if ruta == archivo.replace("\\", "/"):
            del ubicaciones[indice]
            return linea
    return None


def _agrupar(
    hallazgos: list[tuple[str, int | None, str]],
) -> list[tuple[str, int | None, str]]:
    """Colapsa hallazgos que comparten el mismo mensaje base de pytest.

    Un fixture compartido roto revienta en cada test que lo usa: en
    `psf/requests`, el fixture `httpbin` generaba el mismo mensaje mas de 50
    veces, una por test. Sin agrupar, esas 50 lineas del resumen de pytest se
    volvian 50 `Evidence` identicas para un unico error real.
    """
    grupos: dict[str, list[tuple[str, int | None]]] = {}
    for archivo, linea, nota_base in hallazgos:
        grupos.setdefault(nota_base, []).append((archivo, linea))

    resultado = []
    for nota_base, ocurrencias in grupos.items():
        archivo, linea = ocurrencias[0]
        if len(ocurrencias) == 1:
            resultado.append((archivo, linea, nota_base))
            continue
        archivos_unicos = list(dict.fromkeys(a for a, _ in ocurrencias))
        nota = (
            f"{nota_base} (repetido en {len(ocurrencias)} tests) - "
            f"ver {', '.join(archivos_unicos)}"
        )
        resultado.append((archivo, linea, nota))
    return resultado


def verify(ctx: RepoContext) -> VerifierResult:
    if not _is_python_project(ctx.path):
        return VerifierResult(verdict=Verdict.APROBADO, evidence=[])

    result = subprocess.run(
        [sys.executable, "-m", "pytest"],
        cwd=ctx.path,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_SECONDS,
        check=False,
        env=_pytest_env(ctx.path),
        creationflags=_CREATIONFLAGS,
    )

    if result.returncode == 0:
        return VerifierResult(verdict=Verdict.APROBADO, evidence=[])

    output = result.stdout + result.stderr
    lines = output.splitlines()
    error_details = [m.group(1) for m in map(_ERROR_DETAIL_LINE.match, lines) if m]
    fallback_detail = error_details[0] if error_details else "pytest fallo"

    missing_module = next(
        (
            normalize_dependency_name(match.group(1))
            for detail in error_details
            if (match := _MODULE_NOT_FOUND.search(detail))
        ),
        None,
    )
    # El mapeo import->paquete cuenta acá igual que en `deps_check`: mirar
    # solo el nombre del import convertía un fallo de NUESTRO entorno en un
    # NO_SOSTENIBLE del repo auditado. Medido en `arrow-py/arrow`, que
    # declara `python-dateutil` e importa `dateutil`.
    is_declared_missing_dep = missing_module is not None and (
        missing_module in ctx.declared_dependencies
        or declarado_como(missing_module, ctx.declared_dependencies) is not None
    )

    ubicaciones = _ubicaciones(lines)
    hallazgos: list[tuple[str, int | None, str]] = []
    for match in map(_SUMMARY_LINE.match, lines):
        if match is None:
            continue
        archivo = match.group(1)
        nota = (
            _UNDECLARED_DEP_NOTE
            if is_declared_missing_dep
            else (match.group(2) or fallback_detail).strip()
        )
        linea = _tomar_linea(ubicaciones, archivo)
        hallazgos.append((archivo, linea, nota))

    evidence: list[Evidence] = []
    for archivo, linea, nota in _agrupar(hallazgos):
        if linea is None:
            # Decirlo es el punto. Antes salia `line=1` escrito a mano: una
            # ubicacion inventada, en la herramienta que existe para no
            # dejar pasar afirmaciones sin respaldo.
            nota = f"{nota} - {_SIN_UBICACION}"
        evidence.append(
            Evidence(file=archivo, line=0 if linea is None else linea, note=nota)
        )
    if not evidence:
        evidence = [
            Evidence(
                file="pytest",
                line=0,
                note=f"pytest fallo (exit {result.returncode}): {output[-2000:].strip()}",
            )
        ]

    verdict = (
        Verdict.APROBADO_CON_OBSERVACIONES if is_declared_missing_dep else Verdict.NO_SOSTENIBLE
    )
    return VerifierResult(verdict=verdict, evidence=evidence)

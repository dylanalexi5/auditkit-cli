"""Tests de la tabla de API pública por paquete.

El índice se prueba aislado del verificador: es una primitiva de `core/`, no
un verificador, y quien la usa (`readme_check.py`) tiene sus propios tests.
Fixtures inline con `tmp_path`, sin conftest, como el resto del repo.
"""

import dataclasses
from pathlib import Path

import pytest

from auditor.core import symbol_index


def test_construir_encuentra_funciones_clases_y_sus_ubicaciones(tmp_path: Path) -> None:
    paquete = tmp_path / "demo"
    paquete.mkdir()
    (paquete / "__init__.py").write_text("")
    (paquete / "api.py").write_text(
        "import os\n\n\ndef cargar():\n    return os\n\n\nclass Sesion:\n    pass\n",
        encoding="utf-8",
    )

    indice = symbol_index.construir(tmp_path)

    assert indice.resuelve("demo", "cargar") == [("demo/api.py", 4)]
    assert indice.resuelve("demo", "Sesion") == [("demo/api.py", 8)]
    # `os` es un import, no una definicion de este paquete.
    assert indice.resuelve("demo", "os") == []


def test_construir_encuentra_funciones_async(tmp_path: Path) -> None:
    """`readme_check._count_test_functions` solo mira `ast.FunctionDef` y por
    eso no ve los tests `async def`. La tabla no repite ese hueco."""
    (tmp_path / "mod.py").write_text("async def traer():\n    pass\n", encoding="utf-8")

    indice = symbol_index.construir(tmp_path)

    assert indice.resuelve("mod", "traer") == [("mod.py", 1)]


def test_los_metodos_no_son_api_del_paquete(tmp_path: Path) -> None:
    """`paquete.nombre` significa un nombre exportado por el paquete, no
    cualquier metodo enterrado en una clase.

    Control positivo que descubrio esto: se renombro `def echo(` en
    pallets/click y el verificador siguio diciendo que `click.echo` existia,
    porque un metodo suelto con ese nombre alcanzaba."""
    (tmp_path / "mod.py").write_text(
        "class Sesion:\n    def get(self):\n        pass\n", encoding="utf-8"
    )

    indice = symbol_index.construir(tmp_path)

    assert indice.resuelve("mod", "Sesion") == [("mod.py", 1)]
    assert indice.resuelve("mod", "get") == []


def test_los_nombres_de_tests_no_satisfacen_una_cita_al_paquete(tmp_path: Path) -> None:
    """El fallo exacto del control positivo: `click.echo` resolvia contra
    tests/test_utils/test_prompt.py:94. Los tests viven bajo la raiz `tests`,
    asi que no pueden responder por el espacio de nombres de `demo`."""
    paquete = tmp_path / "demo"
    paquete.mkdir()
    (paquete / "__init__.py").write_text("")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_algo.py").write_text("def echo():\n    pass\n", encoding="utf-8")

    indice = symbol_index.construir(tmp_path)

    assert indice.resuelve("demo", "echo") == []
    assert indice.resuelve("tests", "echo") == [("tests/test_algo.py", 1)]


def test_construir_registra_todas_las_ubicaciones_de_un_nombre_repetido(
    tmp_path: Path,
) -> None:
    """Un nombre puede estar definido en varios modulos del mismo paquete.
    Guardar solo el primero perderia la ubicacion que el lector necesita."""
    paquete = tmp_path / "demo"
    paquete.mkdir()
    (paquete / "uno.py").write_text("def comun():\n    pass\n", encoding="utf-8")
    (paquete / "dos.py").write_text("\n\ndef comun():\n    pass\n", encoding="utf-8")

    indice = symbol_index.construir(tmp_path)

    assert sorted(indice.resuelve("demo", "comun")) == [
        ("demo/dos.py", 3),
        ("demo/uno.py", 1),
    ]


def test_construir_incluye_asignaciones_de_modulo(tmp_path: Path) -> None:
    """`requests.codes` es una asignacion de modulo, no una funcion. Sin esto,
    un README que la cite se reportaria como inexistente."""
    (tmp_path / "mod.py").write_text("codes = {'ok': 200}\n", encoding="utf-8")

    indice = symbol_index.construir(tmp_path)

    assert indice.resuelve("mod", "codes") == [("mod.py", 1)]


def test_construir_incluye_asignaciones_anotadas(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text("TIMEOUT: int = 30\n", encoding="utf-8")

    indice = symbol_index.construir(tmp_path)

    assert indice.resuelve("mod", "TIMEOUT") == [("mod.py", 1)]


def test_una_asignacion_dentro_de_una_clase_no_es_api_del_paquete(
    tmp_path: Path,
) -> None:
    (tmp_path / "mod.py").write_text(
        "class C:\n    atributo = 1\n", encoding="utf-8"
    )

    indice = symbol_index.construir(tmp_path)

    assert indice.resuelve("mod", "atributo") == []


def test_construir_registra_los_submodulos_como_nombres(tmp_path: Path) -> None:
    """`requests.exceptions.HTTPError` cita `exceptions`, que es un modulo."""
    paquete = tmp_path / "demo"
    paquete.mkdir()
    (paquete / "__init__.py").write_text("")
    (paquete / "exceptions.py").write_text("class Error:\n    pass\n", encoding="utf-8")

    indice = symbol_index.construir(tmp_path)

    assert indice.resuelve("demo", "exceptions") == [("demo/exceptions.py", 1)]


def test_paquetes_detecta_el_layout_src(tmp_path: Path) -> None:
    """`src/` es layout habitual (psf/black y psf/requests lo usan) y el
    paquete importable es lo que hay adentro, no `src`."""
    (tmp_path / "src" / "demo").mkdir(parents=True)
    (tmp_path / "src" / "demo" / "__init__.py").write_text("")
    (tmp_path / "src" / "demo" / "api.py").write_text(
        "def existe():\n    pass\n", encoding="utf-8"
    )
    (tmp_path / "suelto.py").write_text("def otra():\n    pass\n", encoding="utf-8")

    indice = symbol_index.construir(tmp_path)

    assert "demo" in indice.paquetes
    assert "suelto" in indice.paquetes
    assert "src" not in indice.paquetes
    assert indice.resuelve("demo", "existe") == [("src/demo/api.py", 1)]


def test_construir_ignora_fixtures_de_test(tmp_path: Path) -> None:
    """Mismo criterio que deps_check: tests/data/ y tests/fixtures/ son
    entrada de prueba, no codigo del repo."""
    datos = tmp_path / "tests" / "data"
    datos.mkdir(parents=True)
    (datos / "caso.py").write_text("def no_es_del_repo():\n    pass\n", encoding="utf-8")
    (tmp_path / "real.py").write_text("def si_es():\n    pass\n", encoding="utf-8")

    indice = symbol_index.construir(tmp_path)

    assert indice.resuelve("tests", "no_es_del_repo") == []
    assert indice.resuelve("real", "si_es") == [("real.py", 1)]


def test_construir_saltea_archivos_que_no_parsean(tmp_path: Path) -> None:
    """Un solo archivo roto no puede tumbar el indice: `readme_check` corre
    por defecto sobre repos ajenos, y black tiene .py invalidos a proposito."""
    (tmp_path / "roto.py").write_text("def (((:\n", encoding="utf-8")
    (tmp_path / "sano.py").write_text("def anda():\n    pass\n", encoding="utf-8")

    indice = symbol_index.construir(tmp_path)

    assert indice.resuelve("sano", "anda") == [("sano.py", 1)]


def test_construir_sobre_repo_vacio_no_falla(tmp_path: Path) -> None:
    indice = symbol_index.construir(tmp_path)

    assert indice.publicos == {}
    assert indice.paquetes == frozenset()
    assert indice.truncado is False


def test_construir_corta_en_el_tope_de_archivos(tmp_path: Path) -> None:
    """Tope duro para no colgarse en un monorepo. Literal 3, no
    symbol_index._MAX_ARCHIVOS: comparar contra la constante que el mutante
    altera es tautologico."""
    for i in range(10):
        (tmp_path / f"m{i}.py").write_text(f"def f{i}():\n    pass\n", encoding="utf-8")

    indice = symbol_index.construir(tmp_path, max_archivos=3)

    assert len(indice.archivos_leidos) == 3
    assert indice.truncado is True


def test_construir_no_marca_truncado_si_entra_todo(tmp_path: Path) -> None:
    (tmp_path / "uno.py").write_text("def f():\n    pass\n", encoding="utf-8")

    indice = symbol_index.construir(tmp_path, max_archivos=3)

    assert indice.truncado is False


# ---------------------------------------------------------------------------
# Huecos que encontro el mutation testing. Cada uno mata un mutante concreto
# que sobrevivio a la primera corrida.
# ---------------------------------------------------------------------------


def test_el_indice_es_inmutable(tmp_path: Path) -> None:
    """`frozen=True` -> `frozen=False` sobrevivia. El resto del proyecto usa
    dataclasses congeladas y la evidencia depende de que nadie las reescriba
    a mitad de camino."""
    indice = symbol_index.construir(tmp_path)

    with pytest.raises(dataclasses.FrozenInstanceError):
        indice.truncado = True  # type: ignore[misc]


def test_un_directorio_con_nombre_terminado_en_py_no_pierde_el_sufijo(
    tmp_path: Path,
) -> None:
    """`len(partes) == 1` -> `>= 1` sobrevivia. Con `>=`, a un paquete cuyo
    directorio se llama `pkg.py` se le recortaria el sufijo y quedaria
    indexado como `pkg`, que es un paquete distinto."""
    paquete = tmp_path / "pkg.py"
    paquete.mkdir()
    (paquete / "mod.py").write_text("def f():\n    pass\n", encoding="utf-8")

    indice = symbol_index.construir(tmp_path)

    assert indice.resuelve("pkg.py", "f") == [("pkg.py/mod.py", 1)]
    assert indice.resuelve("pkg", "f") == []


def test_un_fixture_no_corta_el_recorrido_de_los_archivos_que_siguen(
    tmp_path: Path,
) -> None:
    """`continue` -> `break` sobrevivia porque el fixture del otro test queda
    ULTIMO en orden alfabetico. Aca `tests/data/` va antes que `zz.py`, asi
    que un `break` se llevaria puesto el archivo real."""
    datos = tmp_path / "tests" / "data"
    datos.mkdir(parents=True)
    (datos / "caso.py").write_text("def de_fixture():\n    pass\n", encoding="utf-8")
    (tmp_path / "zz.py").write_text("def posterior():\n    pass\n", encoding="utf-8")

    indice = symbol_index.construir(tmp_path)

    assert indice.resuelve("zz", "posterior") == [("zz.py", 1)]


def test_un_archivo_sin_nombre_no_corta_el_recorrido(tmp_path: Path) -> None:
    """`continue` -> `break` en la guarda de `raiz is None`. Un archivo
    llamado `.py` no tiene nombre importable; ordena antes que cualquier otro,
    asi que un `break` dejaria el indice vacio."""
    (tmp_path / ".py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "real.py").write_text("def existe():\n    pass\n", encoding="utf-8")

    indice = symbol_index.construir(tmp_path)

    assert indice.resuelve("real", "existe") == [("real.py", 1)]


def test_el_tope_corta_tambien_por_encima_del_cache_de_enteros(
    tmp_path: Path,
) -> None:
    """`>=` -> `is` sobrevivia con topes chicos: CPython cachea los enteros
    -5..256, asi que `len(leidos) is 3` funciona por accidente. Con un tope
    por encima del cache, `is` compara identidad de dos objetos distintos, da
    siempre False, y el tope no corta nunca -- justo en los repos grandes,
    que son la razon de que el tope exista."""
    for i in range(260):
        (tmp_path / f"m{i:03d}.py").write_text("x = 1\n", encoding="utf-8")

    indice = symbol_index.construir(tmp_path, max_archivos=257)

    assert len(indice.archivos_leidos) == 257
    assert indice.truncado is True


def test_un_modulo_que_ordena_antes_que_init_se_registra(tmp_path: Path) -> None:
    """`!=` -> `>` / `>=` sobrevivia. Comparar strings con `>` en vez de `!=`
    descarta cualquier modulo cuyo nombre ordene antes que "__init__": las
    mayusculas van antes que el guion bajo en ASCII."""
    paquete = tmp_path / "demo"
    paquete.mkdir()
    (paquete / "__init__.py").write_text("")
    (paquete / "API.py").write_text("def f():\n    pass\n", encoding="utf-8")

    indice = symbol_index.construir(tmp_path)

    assert indice.resuelve("demo", "API") == [("demo/API.py", 1)]


def test_init_no_se_registra_como_nombre_del_paquete(tmp_path: Path) -> None:
    """`!=` -> `is not` sobrevivia: comparar strings por identidad da siempre
    True, asi que `__init__` entraba como si fuera un submodulo citable."""
    paquete = tmp_path / "demo"
    paquete.mkdir()
    (paquete / "__init__.py").write_text("")

    indice = symbol_index.construir(tmp_path)

    assert indice.resuelve("demo", "__init__") == []


def test_construir_registra_los_subpaquetes_como_nombres(tmp_path: Path) -> None:
    """`transitions.extensions` cita un DIRECTORIO con `__init__.py`, no un
    archivo. Medido contra `pytransitions/transitions`: su README lo cita y
    el verificador reportaba "'extensions' no esta definido en el paquete
    'transitions'" — un falso positivo sobre un subpaquete que existe."""
    sub = tmp_path / "demo" / "extensions"
    sub.mkdir(parents=True)
    (tmp_path / "demo" / "__init__.py").write_text("", encoding="utf-8")
    (sub / "__init__.py").write_text("", encoding="utf-8")

    indice = symbol_index.construir(tmp_path)

    assert indice.resuelve("demo", "extensions") == [
        ("demo/extensions/__init__.py", 1)
    ]


def test_el_paquete_raiz_no_se_registra_dentro_de_si_mismo(tmp_path: Path) -> None:
    """El `__init__.py` de la raiz tiene como directorio padre al paquete
    mismo: registrarlo dejaria a `demo.demo` resolviendo."""
    paquete = tmp_path / "demo"
    paquete.mkdir()
    (paquete / "__init__.py").write_text("", encoding="utf-8")

    indice = symbol_index.construir(tmp_path)

    assert indice.resuelve("demo", "demo") == []


def test_un_subpaquete_que_ordena_antes_que_el_paquete_se_registra(
    tmp_path: Path,
) -> None:
    """`!=` -> `>` sobrevivia: comparar los nombres con `>` en vez de `!=`
    descarta todo subpaquete cuyo nombre ordene antes que el del paquete
    raiz, que es la mitad de los casos."""
    sub = tmp_path / "demo" / "api"
    sub.mkdir(parents=True)
    (tmp_path / "demo" / "__init__.py").write_text("", encoding="utf-8")
    (sub / "__init__.py").write_text("", encoding="utf-8")

    indice = symbol_index.construir(tmp_path)

    assert indice.resuelve("demo", "api") == [("demo/api/__init__.py", 1)]

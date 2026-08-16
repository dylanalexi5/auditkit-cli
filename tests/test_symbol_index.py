"""Tests de la tabla de API pública por paquete.

El índice se prueba aislado del verificador: es una primitiva de `core/`, no
un verificador, y quien la usa (`readme_check.py`) tiene sus propios tests.
Fixtures inline con `tmp_path`, sin conftest, como el resto del repo.
"""

from pathlib import Path

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

import sys
from pathlib import Path
from unittest.mock import patch

from auditor.core.models import Verdict
from auditor.core.repo_context import RepoContext
from auditor.verifiers import build_check


def _write_pyproject(path: Path) -> None:
    (path / "pyproject.toml").write_text('[project]\nname = "fixture"\nversion = "0.0.1"\n')


def test_verify_fails_on_broken_import(tmp_path: Path) -> None:
    _write_pyproject(tmp_path)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_broken.py").write_text(
        "import nonexistent_module_xyz\n\n\ndef test_thing():\n    assert True\n"
    )

    result = build_check.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.NO_SOSTENIBLE
    assert any(
        "test_broken.py" in e.file and "nonexistent_module_xyz" in e.note
        for e in result.evidence
    )


def test_verify_module_not_found_with_declared_dependency_is_observaciones(
    tmp_path: Path,
) -> None:
    _write_pyproject(tmp_path)
    (tmp_path / "requirements.txt").write_text("nonexistent_module_xyz==1.0.0\n")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_broken.py").write_text(
        "import nonexistent_module_xyz\n\n\ndef test_thing():\n    assert True\n"
    )

    result = build_check.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.APROBADO_CON_OBSERVACIONES
    assert any(
        "test_broken.py" in e.file and "dependencia declarada no instalada" in e.note
        for e in result.evidence
    )


def test_verify_passes_on_working_tests(tmp_path: Path) -> None:
    _write_pyproject(tmp_path)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_ok.py").write_text("def test_thing():\n    assert True\n")

    result = build_check.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.APROBADO
    assert result.evidence == []


def test_verify_non_python_repo_is_aprobado(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# not a python project\n")

    result = build_check.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.APROBADO
    assert result.evidence == []


def test_verify_src_layout_own_package_is_importable(tmp_path: Path) -> None:
    _write_pyproject(tmp_path)
    pkg = tmp_path / "src" / "mypkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("def suma(a, b):\n    return a + b\n")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_suma.py").write_text(
        "from mypkg import suma\n\n\ndef test_suma():\n    assert suma(1, 1) == 2\n"
    )

    result = build_check.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.APROBADO
    assert result.evidence == []


def test_verify_flat_layout_own_package_is_importable(tmp_path: Path) -> None:
    _write_pyproject(tmp_path)
    pkg = tmp_path / "mypkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("VALOR = 42\n")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_valor.py").write_text(
        "from mypkg import VALOR\n\n\ndef test_valor():\n    assert VALOR == 42\n"
    )

    result = build_check.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.APROBADO
    assert result.evidence == []


def test_verify_module_not_found_con_mapeo_conocido_es_observaciones(
    tmp_path: Path,
) -> None:
    """El downgrade comparaba el nombre del IMPORT contra los nombres
    DECLARADOS sin pasar por el mapeo import->paquete. En `arrow-py/arrow`
    eso convertia un fallo de nuestro entorno —no tenemos instalado
    `python-dateutil`— en un NO_SOSTENIBLE del repo auditado.

    El fixture depende de que `dateutil` NO este instalado en el entorno que
    corre los tests: si lo estuviera, pytest pasaria y el test fallaria a la
    vista, no en silencio.
    """
    _write_pyproject(tmp_path)
    (tmp_path / "requirements.txt").write_text("python-dateutil==2.9.0\n")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_broken.py").write_text(
        "import dateutil\n\n\ndef test_thing():\n    assert True\n"
    )

    result = build_check.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.APROBADO_CON_OBSERVACIONES
    assert any(
        "test_broken.py" in e.file and "dependencia declarada no instalada" in e.note
        for e in result.evidence
    )


def test_la_evidencia_apunta_a_la_linea_real_del_fallo(tmp_path: Path) -> None:
    """`line=1` estaba escrito a mano: la evidencia decia que el fallo estaba
    en la primera linea del archivo, que casi nunca es cierto. Es la tercera
    vez que aparece el mismo patron de ubicacion fabricada (antes en
    `_locate_quote` y en el aviso de recorte de `semantic_check`), y es el
    que rompe la promesa central de la herramienta: evidencia verificable."""
    _write_pyproject(tmp_path)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_falla.py").write_text(
        "def test_uno():\n    assert True\n\n\ndef test_dos():\n    x = 1\n    assert x == 2\n",
        encoding="utf-8",
    )

    result = build_check.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.NO_SOSTENIBLE
    assert len(result.evidence) == 1
    # Literal a proposito: `assert x == 2` esta en la linea 7 del fixture.
    assert result.evidence[0].line == 7
    assert result.evidence[0].file == "tests/test_falla.py"


def test_dos_fallos_en_el_mismo_archivo_no_comparten_la_linea(tmp_path: Path) -> None:
    """Reportar la misma linea para los dos seria fabricar una de las dos."""
    _write_pyproject(tmp_path)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_dos.py").write_text(
        "def test_uno():\n    assert 1 == 2\n\n\ndef test_dos():\n    assert 3 == 4\n",
        encoding="utf-8",
    )

    result = build_check.verify(RepoContext.from_path(tmp_path))

    assert sorted(e.line for e in result.evidence) == [2, 6]


def test_sin_ubicacion_en_la_salida_lo_dice_en_vez_de_inventarla(
    tmp_path: Path,
) -> None:
    """Caso real y comun: el fallo ocurre en un fixture de `conftest.py`, asi
    que pytest nombra el archivo de test en el resumen pero las lineas que
    imprime son las de OTROS archivos. No hay ubicacion para el archivo que
    se reporta, y eso se dice en vez de caer en 0 o en 1 en silencio.

    El helper se llama `zhelpers.py` a proposito, no `helpers.py`: ordena
    DESPUES de `test_a.py`. `==` -> `>=` sobrevivia con cualquier nombre que
    ordenara antes, porque la comparacion daba False igual. Con uno que
    ordena despues, el mutante toma la linea del helper y la reporta como si
    fuera del archivo de test — una ubicacion fabricada, que es justo lo que
    este test existe para impedir."""
    _write_pyproject(tmp_path)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "zhelpers.py").write_text(
        'def explota():\n    raise RuntimeError("desde el helper")\n', encoding="utf-8"
    )
    (tests_dir / "conftest.py").write_text(
        "import pytest\n\nfrom zhelpers import explota\n\n\n"
        "@pytest.fixture\ndef roto():\n    explota()\n",
        encoding="utf-8",
    )
    (tests_dir / "test_a.py").write_text(
        "def test_usa_el_fixture(roto):\n    assert True\n", encoding="utf-8"
    )

    result = build_check.verify(RepoContext.from_path(tmp_path))

    assert len(result.evidence) == 1
    assert result.evidence[0].file == "tests/test_a.py"
    assert result.evidence[0].line == 0
    assert "ubicacion no determinada" in result.evidence[0].note


def test_sin_lineas_de_resumen_la_evidencia_no_finge_un_archivo(
    tmp_path: Path,
) -> None:
    """Cuando pytest muere antes de imprimir el resumen —un `conftest.py` que
    no importa, el caso real de `arrow-py/arrow`— no hay archivo ni linea que
    citar. `file="pytest"` y `line=0` son la convencion de "sin ubicacion" del
    proyecto, la misma que usa `_skipped`."""
    _write_pyproject(tmp_path)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "conftest.py").write_text(
        "import modulo_que_no_existe_xyz\n", encoding="utf-8"
    )
    (tests_dir / "test_a.py").write_text("def test_uno():\n    assert True\n", encoding="utf-8")

    result = build_check.verify(RepoContext.from_path(tmp_path))

    assert len(result.evidence) == 1
    assert result.evidence[0].file == "pytest"
    assert result.evidence[0].line == 0


def test_el_mismo_error_en_varios_tests_colapsa_en_una_sola_evidencia(
    tmp_path: Path,
) -> None:
    """Reproduce psf/requests --run-tests: un fixture compartido roto (ahi,
    httpbin) revienta en cada test que lo usa, y el mismo mensaje de pytest
    salia una vez por test -- mas de 50 evidencias identicas para un unico
    error real. Con un fixture roto usado por 3 tests en 2 archivos, tiene
    que quedar UNA evidencia, no tres."""
    _write_pyproject(tmp_path)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "conftest.py").write_text(
        "import pytest\n\n\n@pytest.fixture\ndef roto():\n"
        '    raise RuntimeError("fixture rota")\n',
        encoding="utf-8",
    )
    (tests_dir / "test_a.py").write_text(
        "def test_uno(roto):\n    pass\n\n\ndef test_dos(roto):\n    pass\n",
        encoding="utf-8",
    )
    (tests_dir / "test_b.py").write_text(
        "def test_tres(roto):\n    pass\n", encoding="utf-8"
    )

    result = build_check.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.NO_SOSTENIBLE
    assert len(result.evidence) == 1
    nota = result.evidence[0].note
    assert "repetido en 3 tests" in nota
    assert "tests/test_a.py" in nota
    assert "tests/test_b.py" in nota


def test_verify_suprime_la_ventana_de_consola_en_windows(tmp_path: Path) -> None:
    """`verify()` lanza un pytest real por subprocess. Sin creationflags, un
    proceso sin consola propia adjunta (un worker en background, tal cual
    corre mutation testing real) puede abrir una ventana de consola en
    blanco por cada invocacion -- con cientos de invocaciones esto colgo la
    maquina de verdad, dos veces. CREATE_NO_WINDOW (0x08000000) solo existe
    en Windows; en otros sistemas el valor tiene que ser 0, que
    subprocess.run acepta y ignora sin error."""
    esperado = 0x08000000 if sys.platform == "win32" else 0
    assert build_check._CREATIONFLAGS == esperado

    _write_pyproject(tmp_path)

    class _FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    with patch(
        "auditor.verifiers.build_check.subprocess.run", return_value=_FakeResult()
    ) as mock_run:
        build_check.verify(RepoContext.from_path(tmp_path))

    assert mock_run.call_args.kwargs["creationflags"] == esperado


def test_errores_con_causa_distinta_no_se_mezclan(tmp_path: Path) -> None:
    """El colapso es por mensaje de error, no por "hubo mas de un fallo": dos
    causas distintas siguen siendo dos evidencias."""
    _write_pyproject(tmp_path)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_dos.py").write_text(
        "def test_uno():\n    assert 1 == 2\n\n\ndef test_dos():\n    assert 3 == 4\n",
        encoding="utf-8",
    )

    result = build_check.verify(RepoContext.from_path(tmp_path))

    assert len(result.evidence) == 2

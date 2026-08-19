"""Tests del troceado del codigo para `--ask`.

Fixtures inline con `tmp_path`, sin conftest, como el resto del repo.
"""

import dataclasses
from pathlib import Path

import pytest

from auditor.core import code_chunks


def _escribir(ruta: Path, texto: str) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(texto, encoding="utf-8")


def test_extrae_funciones_de_nivel_superior_con_ubicacion(tmp_path: Path) -> None:
    _escribir(
        tmp_path / "mod.py",
        "import os\n\n\ndef cargar(ruta):\n    return os.path.join(ruta)\n",
    )

    corpus = code_chunks.extraer(tmp_path)

    assert len(corpus.fragmentos) == 1
    frag = corpus.fragmentos[0]
    assert frag.file == "mod.py"
    assert frag.line == 4
    assert "def cargar" in frag.texto
    assert "os.path.join" in frag.texto


def test_extrae_metodos_ademas_de_funciones(tmp_path: Path) -> None:
    """Diferencia deliberada con symbol_index (ADR 0004), que indexa API
    publica y por eso mira solo el nivel superior. La respuesta a "¿donde
    maneja reintentos?" suele vivir en un metodo, no en una funcion suelta."""
    _escribir(
        tmp_path / "mod.py",
        "class Adaptador:\n"
        "    def enviar(self, req):\n"
        "        return self.reintentar(req)\n",
    )

    corpus = code_chunks.extraer(tmp_path)

    ubicaciones = {(f.file, f.line) for f in corpus.fragmentos}
    assert ("mod.py", 1) in ubicaciones  # la clase
    assert ("mod.py", 2) in ubicaciones  # el metodo


def test_extrae_clases_async(tmp_path: Path) -> None:
    _escribir(tmp_path / "mod.py", "async def traer(url):\n    return url\n")

    corpus = code_chunks.extraer(tmp_path)

    assert [(f.file, f.line) for f in corpus.fragmentos] == [("mod.py", 1)]


def test_la_firma_es_corta_y_no_el_cuerpo_entero(tmp_path: Path) -> None:
    """La firma va al reporte para que el lector ubique el fragmento sin
    tener que leer las 40 lineas del cuerpo."""
    _escribir(
        tmp_path / "mod.py",
        "def enviar(request, timeout=None):\n" + "    pass\n" * 20,
    )

    corpus = code_chunks.extraer(tmp_path)

    assert corpus.fragmentos[0].firma == "def enviar(request, timeout=None):"


def test_ignora_fixtures_de_test(tmp_path: Path) -> None:
    """tests/data/ es input de prueba, no codigo del repo: contestaria
    preguntas con archivos que no son del proyecto."""
    _escribir(tmp_path / "tests" / "data" / "caso.py", "def de_fixture():\n    pass\n")
    _escribir(tmp_path / "real.py", "def del_repo():\n    pass\n")

    corpus = code_chunks.extraer(tmp_path)

    assert [f.file for f in corpus.fragmentos] == ["real.py"]


def test_saltea_archivos_que_no_parsean(tmp_path: Path) -> None:
    _escribir(tmp_path / "roto.py", "def (((:\n")
    _escribir(tmp_path / "sano.py", "def anda():\n    pass\n")

    corpus = code_chunks.extraer(tmp_path)

    assert [f.file for f in corpus.fragmentos] == ["sano.py"]


def test_saltea_directorios_que_terminan_en_py(tmp_path: Path) -> None:
    """`rglob("*.py")` tambien devuelve DIRECTORIOS. Leerlos revienta con
    PermissionError en Windows e IsADirectoryError en POSIX -- el mismo bug
    que aparecio en symbol_index (ADR 0004)."""
    (tmp_path / "paquete.py").mkdir()
    _escribir(tmp_path / "paquete.py" / "mod.py", "def adentro():\n    pass\n")

    corpus = code_chunks.extraer(tmp_path)

    assert [f.file for f in corpus.fragmentos] == ["paquete.py/mod.py"]


def test_repo_sin_codigo_devuelve_corpus_vacio(tmp_path: Path) -> None:
    corpus = code_chunks.extraer(tmp_path)

    assert corpus.fragmentos == ()
    assert corpus.truncado is False


def test_el_tope_de_fragmentos_corta_y_lo_declara(tmp_path: Path) -> None:
    """Literal 3, no code_chunks._MAX_FRAGMENTOS: comparar contra la misma
    constante que el mutante altera es tautologico."""
    _escribir(tmp_path / "mod.py", "".join(f"def f{i}():\n    pass\n" for i in range(10)))

    corpus = code_chunks.extraer(tmp_path, max_fragmentos=3)

    assert len(corpus.fragmentos) == 3
    assert corpus.truncado is True


def test_sin_tope_alcanzado_no_se_declara_truncado(tmp_path: Path) -> None:
    _escribir(tmp_path / "mod.py", "def unica():\n    pass\n")

    corpus = code_chunks.extraer(tmp_path, max_fragmentos=3)

    assert corpus.truncado is False


def test_el_texto_de_un_fragmento_gigante_se_recorta(tmp_path: Path) -> None:
    """El modelo trunca a 256 tokens de todos modos; mandar 40 KB de una
    funcion sola es gasto puro."""
    _escribir(
        tmp_path / "mod.py",
        "def gigante():\n" + "    x = 'relleno de la funcion gigante'\n" * 500,
    )

    corpus = code_chunks.extraer(tmp_path)

    assert len(corpus.fragmentos[0].texto) == 1500


# ---------------------------------------------------------------------------
# Huecos que encontro el mutation testing.
# ---------------------------------------------------------------------------


def test_los_fragmentos_y_el_corpus_son_inmutables(tmp_path: Path) -> None:
    """`frozen=True` -> `False` sobrevivia en las dos dataclasses. El resto
    del proyecto usa dataclasses congeladas y la evidencia depende de que
    nadie las reescriba a mitad de camino."""
    _escribir(tmp_path / "mod.py", "def f():\n    pass\n")

    corpus = code_chunks.extraer(tmp_path)

    with pytest.raises(dataclasses.FrozenInstanceError):
        corpus.truncado = True  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        corpus.fragmentos[0].line = 99  # type: ignore[misc]


def test_un_fixture_no_corta_el_recorrido_de_los_archivos_que_siguen(
    tmp_path: Path,
) -> None:
    """`continue` -> `break` sobrevivia porque en el otro test el archivo real
    ordena ANTES que tests/. Aca `zz.py` va despues, asi que un break se lo
    llevaria puesto."""
    _escribir(tmp_path / "tests" / "data" / "caso.py", "def de_fixture():\n    pass\n")
    _escribir(tmp_path / "zz.py", "def posterior():\n    pass\n")

    corpus = code_chunks.extraer(tmp_path)

    assert [f.file for f in corpus.fragmentos] == ["zz.py"]


def test_cuenta_los_archivos_efectivamente_leidos(tmp_path: Path) -> None:
    """`archivos += 1` -> `+= 2` / `+= 0` sobrevivian: el numero se reporta al
    usuario ("37 archivos indexados") y ningun test lo miraba. El archivo roto
    no cuenta, porque no se llego a leer."""
    _escribir(tmp_path / "uno.py", "def a():\n    pass\n")
    _escribir(tmp_path / "dos.py", "def b():\n    pass\n")
    _escribir(tmp_path / "roto.py", "def (((:\n")

    corpus = code_chunks.extraer(tmp_path)

    assert corpus.archivos_leidos == 2


def test_sigue_recorriendo_archivos_mientras_no_haya_truncado(
    tmp_path: Path,
) -> None:
    """`if truncado:` -> `if not truncado:` sobrevivia porque en los demas
    fixtures solo UN archivo aportaba fragmentos. Con la negacion, el
    recorrido corta despues del primero."""
    _escribir(tmp_path / "aaa.py", "def primera():\n    pass\n")
    _escribir(tmp_path / "bbb.py", "def segunda():\n    pass\n")

    corpus = code_chunks.extraer(tmp_path)

    assert [f.firma for f in corpus.fragmentos] == ["def primera():", "def segunda():"]


def test_al_truncar_deja_de_leer_archivos(tmp_path: Path) -> None:
    """`break` -> `continue` en el corte externo: con continue se siguen
    abriendo y parseando archivos que ya no pueden aportar nada, y el conteo
    de "archivos indexados" que ve el usuario queda inflado."""
    for nombre in ("a.py", "b.py", "c.py"):
        _escribir(tmp_path / nombre, "def f():\n    pass\n\n\ndef g():\n    pass\n")

    corpus = code_chunks.extraer(tmp_path, max_fragmentos=2)

    assert corpus.truncado is True
    # `a.py` llena el cupo; `b.py` se abre, se parsea y ahi se corta, asi que
    # cuenta. `c.py` no se toca. Con `continue` en vez de `break` se
    # abririan los tres y el numero que ve el usuario quedaria inflado.
    assert corpus.archivos_leidos == 2


def test_el_tope_corta_tambien_por_encima_del_cache_de_enteros(
    tmp_path: Path,
) -> None:
    """`>=` -> `is` sobrevivia con topes chicos: CPython cachea los enteros
    -5..256, asi que `is` funcionaba por accidente. Con un tope por encima del
    cache compara identidad de objetos distintos, da siempre False, y el tope
    no corta nunca -- justo en los repos grandes, que son su razon de ser."""
    _escribir(
        tmp_path / "mod.py", "".join(f"def f{i}():\n    pass\n" for i in range(300))
    )

    corpus = code_chunks.extraer(tmp_path, max_fragmentos=257)

    assert len(corpus.fragmentos) == 257
    assert corpus.truncado is True

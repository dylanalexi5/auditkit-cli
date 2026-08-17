"""Tests del troceado del codigo para `--ask`.

Fixtures inline con `tmp_path`, sin conftest, como el resto del repo.
"""

from pathlib import Path

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

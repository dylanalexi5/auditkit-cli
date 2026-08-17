import dataclasses
from pathlib import Path

import pytest

from auditor.core import symbol_index
from auditor.core.models import Verdict
from auditor.core.repo_context import RepoContext
from auditor.verifiers import readme_check


def test_verify_flags_false_coverage_claim(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(
        "# demo\n\nThis project has 100% test coverage.\n"
    )

    result = readme_check.verify(RepoContext(path=tmp_path))

    assert result.verdict == Verdict.NO_SOSTENIBLE
    assert any(
        e.file == "README.md" and e.line == 3 and "coverage" in e.note.lower()
        for e in result.evidence
    )


def test_verify_true_coverage_claim_is_aprobado(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(
        "# demo\n\nThis project has 100% test coverage.\n"
    )
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_demo.py").write_text(
        "def test_something():\n    assert True\n"
    )

    result = readme_check.verify(RepoContext(path=tmp_path))

    assert result.verdict == Verdict.APROBADO
    assert result.evidence == []


def test_verify_no_coverage_claim_is_aprobado(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# demo\n\nJust a plain project.\n")

    result = readme_check.verify(RepoContext(path=tmp_path))

    assert result.verdict == Verdict.APROBADO
    assert result.evidence == []


# ---------------------------------------------------------------------------
# Identificadores citados en ejemplos de uso del README.
#
# El cruce es exactamente el que el ADR 0001 prometia y nunca se implemento:
# "ast extrae funciones/clases/entrypoints reales del codigo (verdad)".
# Hasta ahora `readme_check` solo miraba `N% coverage` contra "hay algun
# test_*", que es UNA afirmacion de UNA forma.
# ---------------------------------------------------------------------------


def _repo_demo(tmp_path: Path, readme: str, codigo: str = "def existe():\n    pass\n"):
    """Repo minimo que se declara a si mismo 'demo' y expone un paquete
    `demo/` importable."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    paquete = tmp_path / "demo"
    paquete.mkdir()
    (paquete / "__init__.py").write_text(codigo, encoding="utf-8")
    (tmp_path / "README.md").write_text(readme, encoding="utf-8")
    return RepoContext.from_path(tmp_path)


def test_identificador_de_ejemplo_que_no_existe_es_hallazgo(tmp_path: Path) -> None:
    """El caso que motiva todo esto: el README muestra como usar el proyecto,
    y la funcion del ejemplo no existe en el codigo. El ejemplo no corre."""
    ctx = _repo_demo(
        tmp_path,
        "# demo\n\nUso:\n\n```python\nfrom demo import inexistente\n```\n",
    )

    result = readme_check.verify(ctx)

    assert result.verdict == Verdict.APROBADO_CON_OBSERVACIONES
    assert len(result.evidence) == 1
    hallazgo = result.evidence[0]
    assert hallazgo.file == "README.md"
    assert hallazgo.line == 6
    assert "inexistente" in hallazgo.note


def test_identificador_de_ejemplo_que_existe_no_es_hallazgo(tmp_path: Path) -> None:
    ctx = _repo_demo(
        tmp_path,
        "# demo\n\n```python\nfrom demo import existe\n```\n",
    )

    result = readme_check.verify(ctx)

    assert result.verdict == Verdict.APROBADO
    assert result.evidence == []


def test_atributo_del_proyecto_se_revisa(tmp_path: Path) -> None:
    """La otra forma de citar: `demo.algo(...)` en vez de `from demo import`."""
    ctx = _repo_demo(
        tmp_path,
        "# demo\n\n```python\nimport demo\ndemo.no_esta()\n```\n",
    )

    result = readme_check.verify(ctx)

    assert [e.line for e in result.evidence] == [5]
    assert "no_esta" in result.evidence[0].note


def test_no_se_revisan_identificadores_ajenos_al_proyecto(tmp_path: Path) -> None:
    """El limite que hace usable esto: solo se revisa lo que el README le
    atribuye a ESTE repo. Un ejemplo que muestra stdlib o una libreria de
    terceros no se toca -- `os.path.join` no vive aca y decir que "no existe"
    seria una acusacion falsa."""
    ctx = _repo_demo(
        tmp_path,
        "# demo\n\n```python\nimport os\nimport pandas as pd\n"
        "os.path.join('a', 'b')\npd.DataFrame()\nrequests.get('...')\n```\n",
    )

    result = readme_check.verify(ctx)

    assert result.verdict == Verdict.APROBADO
    assert result.evidence == []


def test_se_revisan_backticks_sueltos(tmp_path: Path) -> None:
    ctx = _repo_demo(tmp_path, "# demo\n\nUsa `demo.fantasma()` para empezar.\n")

    result = readme_check.verify(ctx)

    assert [e.line for e in result.evidence] == [3]
    assert "fantasma" in result.evidence[0].note


def test_la_prosa_fuera_de_codigo_no_se_revisa(tmp_path: Path) -> None:
    """Sin backticks ni fence no hay ejemplo de uso, hay prosa. "demo.algo"
    en una oracion puede ser cualquier cosa; tratarla como codigo abre la
    puerta a falsos positivos sin ganar nada."""
    ctx = _repo_demo(tmp_path, "# demo\n\nAntes esto se llamaba demo.viejo_nombre.\n")

    result = readme_check.verify(ctx)

    assert result.evidence == []


def test_los_identificadores_faltantes_nunca_llegan_a_no_sostenible(
    tmp_path: Path,
) -> None:
    """Techo deliberado. La afirmacion de coverage si llega a NO_SOSTENIBLE
    porque "no hay una sola funcion de test" no admite lectura alternativa.
    Un identificador ausente si la admite (se genera dinamicamente, es API
    planeada, viene de una extension C), asi que hasta medirlo sobre repos
    reales se queda en observacion."""
    ctx = _repo_demo(
        tmp_path,
        "# demo\n\n```python\nfrom demo import a, b, c\n```\n",
    )

    result = readme_check.verify(ctx)

    assert len(result.evidence) == 3
    assert result.verdict == Verdict.APROBADO_CON_OBSERVACIONES


def test_el_chequeo_de_coverage_sigue_mandando_sobre_el_veredicto(
    tmp_path: Path,
) -> None:
    """Los dos chequeos conviven: el peor veredicto gana, y el de coverage
    es el unico que puede declarar NO_SOSTENIBLE."""
    ctx = _repo_demo(
        tmp_path,
        "# demo\n\n100% test coverage.\n\n```python\nfrom demo import inexistente\n```\n",
    )

    result = readme_check.verify(ctx)

    assert result.verdict == Verdict.NO_SOSTENIBLE
    assert len(result.evidence) == 2


def test_un_bloque_shell_no_es_un_ejemplo_de_uso_de_python(tmp_path: Path) -> None:
    """Falso positivo real, medido contra psf/requests antes de este arreglo.

    Su README.md:65 tiene un bloque ```shell con
    `git clone ... https://github.com/psf/requests.git`, y salia reportado
    como "el ejemplo cita 'git' pero no existe". Dos errores encadenados: un
    bloque shell no es un ejemplo de uso de la API, y `requests.git` es el
    final de una URL, no un atributo."""
    ctx = _repo_demo(
        tmp_path,
        "# demo\n\n```shell\ngit clone https://github.com/x/demo.git\n```\n",
    )

    result = readme_check.verify(ctx)

    assert result.verdict == Verdict.APROBADO
    assert result.evidence == []


def test_una_url_no_es_un_atributo(tmp_path: Path) -> None:
    """La otra mitad del mismo falso positivo, aislada: aunque el bloque SI
    sea python, un dominio o una ruta que contenga el nombre del proyecto no
    es una cita de su API."""
    ctx = _repo_demo(
        tmp_path,
        "# demo\n\n```python\nURL = 'https://github.com/x/demo.git'\n"
        "RUTA = 'vendor/demo.tarball'\n```\n",
    )

    result = readme_check.verify(ctx)

    assert result.evidence == []


def test_una_url_como_argumento_no_tapa_la_cita(tmp_path: Path) -> None:
    """Regresion de un arreglo previo que se paso de largo.

    El primer intento de filtrar URLs miraba el token entero sin espacios, y
    en `demo.get('https://httpbin.org/x')` ese token incluye la URL del
    ARGUMENTO. Resultado medido: psf/requests pasaba de 1 falso positivo a 0
    citas revisadas -- su unico ejemplo en python es exactamente esa forma.
    Lo que descalifica una cita es que el nombre del proyecto este dentro de
    una ruta, no que haya una URL en la misma linea."""
    ctx = _repo_demo(
        tmp_path,
        "# demo\n\n```python\n>>> demo.get('https://httpbin.org/basic-auth/u/p')\n```\n",
        codigo="def get(url):\n    pass\n",
    )

    result = readme_check.verify(ctx)

    assert result.evidence == []


def test_una_url_como_argumento_sigue_detectando_lo_que_falta(tmp_path: Path) -> None:
    """La otra cara: que la cita se revise de verdad y no que se acepte
    siempre. Mismo README que el test anterior, sin la funcion en el codigo."""
    ctx = _repo_demo(
        tmp_path,
        "# demo\n\n```python\n>>> demo.get('https://httpbin.org/basic-auth/u/p')\n```\n",
        codigo="def otra_cosa():\n    pass\n",
    )

    result = readme_check.verify(ctx)

    assert [e.line for e in result.evidence] == [4]
    assert "get" in result.evidence[0].note


def test_un_fence_sin_lenguaje_no_se_revisa(tmp_path: Path) -> None:
    """Decision conservadora: un fence sin etiqueta puede ser shell, texto o
    salida de consola. Un falso "esto no existe" es una acusacion; una
    revision de menos es solo un hueco. Se prefiere el hueco."""
    ctx = _repo_demo(tmp_path, "# demo\n\n```\ndemo.no_esta()\n```\n")

    result = readme_check.verify(ctx)

    assert result.evidence == []


# ---------------------------------------------------------------------------
# Huecos que encontro el mutation testing. Cada uno mata un mutante concreto
# que sobrevivio a la primera corrida.
# ---------------------------------------------------------------------------


def test_la_linea_se_cuenta_desde_el_principio_del_readme(tmp_path: Path) -> None:
    """`count("\\n", 0, offset)` -> `count("\\n", 1, offset)` sobrevivia. El
    README empieza con salto de linea a proposito: con cualquier otro texto
    los dos arrancan en el mismo punto util y la diferencia queda invisible."""
    ctx = _repo_demo(tmp_path, "\n# demo\n\n```python\nfrom demo import fantasma\n```\n")

    result = readme_check.verify(ctx)

    assert [e.line for e in result.evidence] == [5]


def test_una_url_que_sigue_despues_del_nombre_no_es_un_atributo(
    tmp_path: Path,
) -> None:
    """La regla del caracter POSTERIOR, que ningun test ejercitaba: en
    `demo.example.com/x` lo que descalifica la cita es la barra que sigue, no
    un separador previo (antes de `demo` hay una comilla). Sin ella
    sobrevivian los ocho mutantes de esa comparacion."""
    ctx = _repo_demo(tmp_path, "# demo\n\n```python\nURL = 'demo.example.com/api'\n```\n")

    result = readme_check.verify(ctx)

    assert result.evidence == []


def test_un_import_de_submodulo_se_atribuye_al_paquete_raiz(tmp_path: Path) -> None:
    """`modulo.split(".")[0]` -> `[-1]` sobrevivia. Con `[-1]`, un
    `from demo.sub import X` se atribuiria al paquete `sub`, que no existe, y
    la cita se saltearia por no estar en el espacio propio."""
    ctx = _repo_demo(
        tmp_path,
        "# demo\n\n```python\nfrom demo.sub import fantasma\n```\n",
    )

    result = readme_check.verify(ctx)

    # Dos citas, las dos ausentes: el submodulo `sub` y el nombre `fantasma`.
    # Lo que mata al mutante es que `fantasma` este atribuido a `demo` y no a
    # `sub` -- con `[-1]` el paquete seria `sub`, que no esta en el espacio
    # propio, y la cita del import se saltearia entera.
    assert [e.line for e in result.evidence] == [4, 4]
    assert any("demo.fantasma" in e.note for e in result.evidence)


def test_un_import_ajeno_no_corta_la_revision_de_los_que_siguen(
    tmp_path: Path,
) -> None:
    """`continue` -> `break` en la guarda de paquete ajeno."""
    ctx = _repo_demo(
        tmp_path,
        "# demo\n\n```python\nfrom os import path\nfrom demo import fantasma\n```\n",
    )

    result = readme_check.verify(ctx)

    assert [e.line for e in result.evidence] == [5]


def test_un_atributo_ajeno_no_corta_la_revision_de_los_que_siguen(
    tmp_path: Path,
) -> None:
    """`continue` -> `break` en la guarda de atributo ajeno."""
    ctx = _repo_demo(
        tmp_path,
        "# demo\n\n```python\nos.path.join('a')\ndemo.fantasma()\n```\n",
    )

    result = readme_check.verify(ctx)

    assert [e.line for e in result.evidence] == [5]


def test_una_url_no_corta_la_revision_de_los_que_siguen(tmp_path: Path) -> None:
    """`continue` -> `break` en la guarda de URL."""
    ctx = _repo_demo(
        tmp_path,
        "# demo\n\n```python\nU = 'https://x.io/demo.git'\ndemo.fantasma()\n```\n",
    )

    result = readme_check.verify(ctx)

    assert [e.line for e in result.evidence] == [5]


def test_una_cita_repetida_no_corta_las_distintas_que_siguen(
    tmp_path: Path,
) -> None:
    """`continue` -> `break` en la deduplicacion. La primera y la segunda son
    la misma cita en la misma linea; la tercera es distinta y tiene que salir
    igual."""
    ctx = _repo_demo(
        tmp_path,
        "# demo\n\n```python\ndemo.uno() ; demo.uno()\ndemo.dos()\n```\n",
    )

    result = readme_check.verify(ctx)

    assert [(e.line, "uno" in e.note, "dos" in e.note) for e in result.evidence] == [
        (4, True, False),
        (5, False, True),
    ]


def test_con_el_indice_truncado_no_se_acusa_de_nada(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Con el indice cortado por el tope, "no esta en la tabla" significa "no
    lo miramos", no "no existe". Se deja constancia sin ubicacion inventada.

    Los mutantes `line=0` -> `line=1` / `line=-1` sobrevivian porque ningun
    test de este modulo llegaba a la rama de truncado."""
    ctx = _repo_demo(
        tmp_path,
        "# demo\n\n```python\nfrom demo import fantasma\n```\n",
    )
    original = symbol_index.construir
    monkeypatch.setattr(
        symbol_index,
        "construir",
        lambda root, **kw: dataclasses.replace(original(root), truncado=True),
    )

    result = readme_check.verify(ctx)

    assert len(result.evidence) == 1
    assert result.evidence[0].file == "(sin verificar)"
    assert result.evidence[0].line == 0
    assert "no se verificaron" in result.evidence[0].note


def test_sin_pyproject_el_paquete_importable_del_repo_alcanza(tmp_path: Path) -> None:
    """Sin pyproject no sabemos como se declara el proyecto, pero el paquete
    importable que hay en el repo alcanza para saber que `demo.x` le
    pertenece. Sin esto, cualquier repo sin pyproject quedaria sin revisar."""
    paquete = tmp_path / "demo"
    paquete.mkdir()
    (paquete / "__init__.py").write_text("def existe():\n    pass\n", encoding="utf-8")
    (tmp_path / "README.md").write_text(
        "# sin pyproject\n\n```python\ndemo.inexistente()\n```\n", encoding="utf-8"
    )

    result = readme_check.verify(RepoContext.from_path(tmp_path))

    assert [e.line for e in result.evidence] == [4]

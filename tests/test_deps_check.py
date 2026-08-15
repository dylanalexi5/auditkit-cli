import ast
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from auditor.core.models import Verdict
from auditor.core.repo_context import RepoContext
from auditor.verifiers import deps_check


def test_verify_ignores_fake_imports_in_test_fixture_files(tmp_path: Path) -> None:
    """Reproduce psf/black tests/data/cases/*.py: archivos .py reales cuyo
    CONTENIDO es codigo de ejemplo usado como input de un formateador, con
    imports inventados (import foo/hello) que no son dependencias de nadie."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\nversion = "0.0.1"\ndependencies = []\n'
    )
    fixtures_dir = tmp_path / "tests" / "data" / "cases"
    fixtures_dir.mkdir(parents=True)
    (fixtures_dir / "collections.py").write_text("import foo\nimport hello\n")

    with patch("auditor.verifiers.deps_check._run_pip_audit", return_value=[]):
        result = deps_check.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.APROBADO
    assert result.evidence == []


def test_verify_ignores_imports_inside_type_checking_guard(tmp_path: Path) -> None:
    """Reproduce pallets/click: `if t.TYPE_CHECKING: import typing_extensions`
    en src/click/core.py - nunca corre en runtime, no es una dependencia."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\nversion = "0.0.1"\ndependencies = []\n'
    )
    (tmp_path / "app.py").write_text(
        "import typing as t\n\nif t.TYPE_CHECKING:\n    import typing_extensions\n"
    )

    with patch("auditor.verifiers.deps_check._run_pip_audit", return_value=[]):
        result = deps_check.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.APROBADO
    assert result.evidence == []


def test_verify_still_reports_undeclared_import_outside_type_checking(
    tmp_path: Path,
) -> None:
    """El guard TYPE_CHECKING no debe volverse una forma de esconder un
    import real sin declarar - solo lo que esta genuinamente adentro del
    guard se ignora."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\nversion = "0.0.1"\ndependencies = []\n'
    )
    (tmp_path / "app.py").write_text("import totally_undeclared_thing\n")

    with patch("auditor.verifiers.deps_check._run_pip_audit", return_value=[]):
        result = deps_check.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.NO_SOSTENIBLE
    assert any("totally_undeclared_thing" in e.note for e in result.evidence)


def test_extract_requirements_rejects_vcs_and_url_lines(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text(
        "click>=8.0\n"
        "git+https://attacker.example/evil.git#egg=evil\n"
        "https://attacker.example/pkg.whl\n"
        "hg+https://attacker.example/evil\n"
    )

    entries = deps_check._extract_requirements(tmp_path)

    assert [raw for _, _, raw in entries] == ["click>=8.0"]


def test_extract_requirements_reads_poetry_lock(tmp_path: Path) -> None:
    (tmp_path / "poetry.lock").write_text(
        "[[package]]\n"
        'name = "fastapi"\n'
        'version = "0.115.0"\n'
        "[[package]]\n"
        'name = "click"\n'
        'version = "8.1.7"\n'
    )

    entries = deps_check._extract_requirements(tmp_path)

    assert sorted(raw for _, _, raw in entries) == ["click==8.1.7", "fastapi==0.115.0"]


def test_verify_poetry_declared_import_is_not_flagged_as_undeclared(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.poetry.dependencies]\npython = \"^3.11\"\nfastapi = \"^0.115.0\"\n"
    )
    (tmp_path / "app.py").write_text("import fastapi\n\nfastapi.FastAPI()\n")

    result = deps_check.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.APROBADO
    assert result.evidence == []


def test_run_pip_audit_returns_none_on_timeout() -> None:
    entries = [("requirements.txt", 1, "click>=8.0")]
    with patch(
        "auditor.verifiers.deps_check.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="pip-audit", timeout=120),
    ):
        assert deps_check._run_pip_audit(entries) is None


def test_verify_reports_observaciones_when_pip_audit_times_out(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("click>=8.0\n")
    (tmp_path / "app.py").write_text("import click\n\nclick.echo('hi')\n")

    with patch(
        "auditor.verifiers.deps_check.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="pip-audit", timeout=120),
    ):
        result = deps_check.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.APROBADO_CON_OBSERVACIONES
    assert any(
        "pip-audit" in e.file and "no pudo completarse" in e.note for e in result.evidence
    )


def test_verify_used_import_with_known_mapping_is_observaciones(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("scikit-learn==1.5.0\n")
    (tmp_path / "app.py").write_text("import sklearn\n\nsklearn.__version__\n")

    result = deps_check.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.APROBADO_CON_OBSERVACIONES
    assert any(
        "sklearn" in e.note and "scikit-learn" in e.note and "mapeo conocido" in e.note
        for e in result.evidence
    )


def test_verify_real_known_vulnerability_is_no_sostenible(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("pyyaml==5.3\n")
    (tmp_path / "app.py").write_text("import yaml\n\nyaml.safe_load('a: 1')\n")

    result = deps_check.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.NO_SOSTENIBLE
    assert any("pyyaml" in e.note.lower() and "vulnerabilidad" in e.note.lower() for e in result.evidence)


def test_verify_declared_but_unused_is_observaciones(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("click>=8.0\n")
    (tmp_path / "app.py").write_text("print('hello')\n")

    result = deps_check.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.APROBADO_CON_OBSERVACIONES
    assert any(
        "click" in e.note and "declarado" in e.note and "no se usa" in e.note
        for e in result.evidence
    )


def test_verify_used_import_without_declaration_or_mapping_is_no_sostenible(
    tmp_path: Path,
) -> None:
    (tmp_path / "requirements.txt").write_text("click>=8.0\n")
    (tmp_path / "app.py").write_text("import totally_undeclared_thing\n")

    result = deps_check.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.NO_SOSTENIBLE
    assert any("totally_undeclared_thing" in e.note for e in result.evidence)


def test_verify_clean_repo_is_aprobado(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("click>=8.0\n")
    (tmp_path / "app.py").write_text("import click\n\nclick.echo('hi')\n")

    result = deps_check.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.APROBADO
    assert result.evidence == []


def test_verify_without_dependency_files_does_not_cite_a_path(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("import totally_undeclared_thing\n")

    result = deps_check.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.NO_SOSTENIBLE
    for item in result.evidence:
        assert item.file == deps_check._NO_DEPS_FILE, (
            f"la evidencia cita '{item.file}', que no existe en el repo"
        )


def test_verify_does_not_cite_requirements_txt_when_repo_has_none(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\n'
        'name = "fixture"\n'
        'version = "0.0.1"\n'
        'dependencies = ["peppercorn"]\n'
        '[project.optional-dependencies]\n'
        'dev = ["check-manifest"]\n'
    )
    (tmp_path / "app.py").write_text("import totally_undeclared_thing\n")

    with patch("auditor.verifiers.deps_check._run_pip_audit", return_value=[]):
        result = deps_check.verify(RepoContext.from_path(tmp_path))

    for item in result.evidence:
        assert (tmp_path / item.file).is_file(), (
            f"la evidencia cita '{item.file}', que no existe en el repo"
        )


def test_verify_src_layout_own_package_is_not_undeclared(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\nversion = "0.0.1"\ndependencies = []\n'
    )
    pkg = tmp_path / "src" / "mypkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("def suma(a, b):\n    return a + b\n")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_suma.py").write_text(
        "from mypkg import suma\n\n\ndef test_suma():\n    assert suma(1, 1) == 2\n"
    )

    result = deps_check.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.APROBADO
    assert result.evidence == []


def test_verify_transitive_pin_with_via_comment_is_not_flagged_as_unused(
    tmp_path: Path,
) -> None:
    (tmp_path / "requirements.txt").write_text(
        "click>=8.0\nattrs==25.3.0          # via click\n"
    )
    (tmp_path / "app.py").write_text("import click\n\nclick.echo('hi')\n")

    with patch("auditor.verifiers.deps_check._run_pip_audit", return_value=[]):
        result = deps_check.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.APROBADO
    assert result.evidence == []


def test_verify_cli_only_dependency_is_not_flagged_as_unused(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("ruff>=0.6\ncoverage>=7.0\nnox>=2024.4.15\n")
    (tmp_path / "app.py").write_text("print('hola')\n")

    with patch("auditor.verifiers.deps_check._run_pip_audit", return_value=[]):
        result = deps_check.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.APROBADO
    assert result.evidence == []


def test_verify_undeclared_build_tool_import_is_observaciones(tmp_path: Path) -> None:
    """noxfile.py importa nox sin declararlo: es la forma de pypa/sampleproject.
    No es un repo roto, pero sigue siendo un dato a revisar - correr ese script
    exige instalar nox aparte."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\nversion = "0.0.1"\ndependencies = []\n'
    )
    (tmp_path / "noxfile.py").write_text(
        "import nox\n\n\n@nox.session\ndef tests(session):\n    session.run('pytest')\n"
    )

    result = deps_check.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.APROBADO_CON_OBSERVACIONES
    assert any(
        "nox" in item.note and "herramienta" in item.note for item in result.evidence
    ), f"se esperaba una observación explicando por qué nox no está declarado: {result.evidence}"


def test_verify_undeclared_unknown_import_is_still_no_sostenible(tmp_path: Path) -> None:
    """Guarda: la excepción es solo para herramientas de build conocidas."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\nversion = "0.0.1"\ndependencies = []\n'
    )
    (tmp_path / "app.py").write_text("import totally_undeclared_thing\n")

    result = deps_check.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.NO_SOSTENIBLE


# --- Mutation-testing hardening: cierra gaps reales encontrados corriendo
# cosmic-ray sobre deps_check.py (293 mutantes candidatos, 91 sobrevivientes
# en la primera ronda, 24 en la segunda tras esta bateria). Los equivalentes
# documentados (no se testean, verificados de forma empirica - no hay
# diferencia de comportamiento observable posible):
#   - linea 156: `list[dict] | None` es una anotacion de tipo de retorno -
#     bajo PEP 649 (Python 3.14) las anotaciones son lazy, nunca se evaluan
#     salvo que algo acceda a __annotations__. Mutar el operador `|` ahi no
#     cambia ninguna ejecucion real.
#   - linea 183: `text=True` en subprocess.run - el unico uso de
#     result.stdout es `json.loads(result.stdout)`, que acepta str o bytes
#     por igual. Mutarlo a text=False no cambia el resultado.
#   - linea 190: `req_path.unlink(missing_ok=True)` - mismo patron ya
#     documentado en secrets.py: solo importa bajo una race condition de
#     borrado externo entre la creacion del temp file y el unlink, no
#     reproducible de forma determinista en un test.
#   - lineas 225/227, mutante Eq->Is: CPython interna los identificadores que
#     vienen de parsear codigo fuente (ast.Name.id / ast.Attribute.attr) y
#     los literales de string cortos del propio codigo - `test.id is
#     "TYPE_CHECKING"` y `test.id == "TYPE_CHECKING"` dan el mismo resultado
#     siempre para este caso especifico. Confirmado con
#     `ast.parse("if t.TYPE_CHECKING: pass").body[0].test.attr is "TYPE_CHECKING"`
#     -> True.
#   - linea 248, mutante Eq->LtE: `node.level` de un ast.ImportFrom nunca es
#     negativo (0 para absoluto, 1+ para relativo) - `level <= 0` y
#     `level == 0` son la misma condicion para todo valor posible.
#   - linea 303, mutante Or->And en `not stripped or stripped.startswith("#")
#     or not _VIA_COMMENT.search(stripped)`: la mutacion real que cosmic-ray
#     aplica solo cambia como se combinan las primeras dos clausulas
#     (`(not stripped and startswith) or not search`, no las tres). El unico
#     caso donde esas dos clausulas divergen es cuando la linea empieza con
#     "#" (comentario puro) - pero en ese caso `_NAME_TOKEN.match(stripped)`
#     nunca matchea de todos modos (el regex no acepta "#" como primer
#     caracter), asi que saltee o no la linea, el resultado final (el set
#     `names`) es identico. Verificado aplicando la mutacion real via
#     `cosmic_ray.mutating.apply_mutation` y confirmando que ningun input
#     produce una diferencia observable.


def test_locate_returns_correct_entry_among_multiple_when_normalized_name_matches() -> None:
    entries = [
        ("requirements.txt", 1, "click>=8.0"),
        ("requirements.txt", 2, "flask>=2.0"),
        ("requirements.txt", 3, "requests>=2.0"),
    ]

    assert deps_check._locate(entries, "flask", Path(".")) == ("requirements.txt", 2)


def test_locate_falls_back_when_no_entry_matches(tmp_path: Path) -> None:
    entries = [("requirements.txt", 1, "zzz>=1.0")]

    assert deps_check._locate(entries, "aaa", tmp_path) == deps_check._deps_file_fallback(
        tmp_path
    )


def test_run_pip_audit_uses_the_configured_timeout() -> None:
    entries = [("requirements.txt", 1, "click>=8.0")]
    with patch("auditor.verifiers.deps_check.subprocess.run") as mock_run:
        mock_run.return_value.stdout = "{}"
        deps_check._run_pip_audit(entries)

    assert mock_run.call_args.kwargs["timeout"] == 120


def test_parse_poetry_lock_survives_malformed_toml(tmp_path: Path) -> None:
    (tmp_path / "poetry.lock").write_text("not = [valid toml")

    assert deps_check._parse_poetry_lock(tmp_path) == []


def test_parse_poetry_lock_skips_entry_missing_only_version_but_keeps_next(
    tmp_path: Path,
) -> None:
    (tmp_path / "poetry.lock").write_text(
        "[[package]]\n"
        'name = "incomplete"\n'
        "[[package]]\n"
        'name = "fastapi"\n'
        'version = "0.115.0"\n'
    )

    entries = deps_check._parse_poetry_lock(tmp_path)

    assert entries == [("poetry.lock", 1, "fastapi==0.115.0")]


def test_extract_requirements_reports_the_real_line_number_not_always_one(
    tmp_path: Path,
) -> None:
    (tmp_path / "requirements.txt").write_text("# comment\nclick>=8.0\nflask>=2.0\n")

    entries = deps_check._extract_requirements(tmp_path)

    assert ("requirements.txt", 3, "flask>=2.0") in entries


def test_extract_requirements_reads_pep621_project_dependencies(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\nversion = "0.0.1"\ndependencies = ["reallib>=1.0"]\n'
    )

    entries = deps_check._extract_requirements(tmp_path)

    assert ("pyproject.toml", 1, "reallib>=1.0") in entries


def test_deps_file_fallback_returns_line_one_for_the_file_it_finds(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "fixture"\n')

    assert deps_check._deps_file_fallback(tmp_path) == ("pyproject.toml", 1)


def test_deps_file_fallback_returns_line_zero_when_nothing_exists(tmp_path: Path) -> None:
    assert deps_check._deps_file_fallback(tmp_path) == (deps_check._NO_DEPS_FILE, 0)


def test_deps_file_fallback_prefers_requirements_txt_over_pyproject(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("click>=8.0\n")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "fixture"\n')

    assert deps_check._deps_file_fallback(tmp_path) == ("requirements.txt", 1)


def test_is_type_checking_guard_false_for_a_name_that_sorts_after() -> None:
    """El nombre ordena alfabeticamente DESPUES de "TYPE_CHECKING" a
    proposito - distingue una comparacion de igualdad real de un `>`/`>=`
    que coincidiria por casualidad con un nombre posterior en el alfabeto."""
    test_node = ast.parse("if ZZZ_OTHER_FLAG:\n    pass\n").body[0].test

    assert deps_check._is_type_checking_guard(test_node) is False


def test_is_type_checking_guard_false_for_a_name_that_sorts_before() -> None:
    """Mismo motivo que el test anterior, pero para el otro sentido: cubre
    `<`/`<=`."""
    test_node = ast.parse("if AAA_OTHER_FLAG:\n    pass\n").body[0].test

    assert deps_check._is_type_checking_guard(test_node) is False


def test_is_type_checking_guard_false_for_an_attribute_that_sorts_after() -> None:
    test_node = ast.parse("if t.ZZZ_OTHER_FLAG:\n    pass\n").body[0].test

    assert deps_check._is_type_checking_guard(test_node) is False


def test_is_type_checking_guard_false_for_an_attribute_that_sorts_before() -> None:
    test_node = ast.parse("if t.AAA_OTHER_FLAG:\n    pass\n").body[0].test

    assert deps_check._is_type_checking_guard(test_node) is False


def test_is_type_checking_guard_false_for_a_call_expression() -> None:
    test_node = ast.parse("if some_call():\n    pass\n").body[0].test

    assert deps_check._is_type_checking_guard(test_node) is False


def test_top_level_imports_visits_the_else_branch_of_a_type_checking_guard(
    tmp_path: Path,
) -> None:
    (tmp_path / "app.py").write_text(
        "import typing as t\n\nif t.TYPE_CHECKING:\n    import skip_this\nelse:\n"
        "    import else_branch_real\n"
    )

    names = deps_check._top_level_imports(tmp_path)

    assert "else_branch_real" in names
    assert "skip_this" not in names


def test_top_level_imports_takes_only_the_top_level_package_of_dotted_and_relative_imports(
    tmp_path: Path,
) -> None:
    (tmp_path / "app.py").write_text(
        "import os.path\nfrom email.mime.text import MIMEText\nfrom .sibling import thing\n"
    )

    assert deps_check._top_level_imports(tmp_path) == {"os", "email"}


def test_top_level_imports_ignores_a_tests_directory_that_is_not_data_or_fixtures(
    tmp_path: Path,
) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_real.py").write_text("import real_undeclared_thing\n")

    assert "real_undeclared_thing" in deps_check._top_level_imports(tmp_path)


def test_top_level_imports_keeps_scanning_after_a_fixture_file_and_a_syntax_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El orden real de Path.rglob() no esta garantizado - se fuerza a mano
    para que el archivo de fixture (skip por _is_test_fixture_path) y el
    archivo con error de sintaxis (skip por except SyntaxError) se visiten
    ANTES que el archivo real. Si cualquiera de los dos `continue` fuera
    `break`, real.py nunca se visitaria."""
    fixture_dir = tmp_path / "tests" / "data"
    fixture_dir.mkdir(parents=True)
    fixture_file = fixture_dir / "fixture.py"
    fixture_file.write_text("import fixture_only_import\n")

    broken_file = tmp_path / "broken.py"
    broken_file.write_text("def bad(:\n")

    real_file = tmp_path / "real.py"
    real_file.write_text("import real_undeclared_thing\n")

    original_rglob = Path.rglob

    def ordered_rglob(self: Path, pattern: str):
        if self == tmp_path and pattern == "*.py":
            return iter([fixture_file, broken_file, real_file])
        return original_rglob(self, pattern)

    monkeypatch.setattr(Path, "rglob", ordered_rglob)

    names = deps_check._top_level_imports(tmp_path)

    assert "real_undeclared_thing" in names
    assert "fixture_only_import" not in names


def test_scan_dir_for_modules_only_counts_py_files_and_real_packages(tmp_path: Path) -> None:
    """readme.txt (suffix ".txt", ordena despues de ".py") y config.json
    (suffix ".json", ordena antes de ".py") cubren los dos sentidos de una
    comparacion de orden que coincidiria por casualidad con la igualdad."""
    (tmp_path / "realmod.py").write_text("x = 1\n")
    (tmp_path / "readme.txt").write_text("not python\n")
    (tmp_path / "config.json").write_text("{}\n")
    (tmp_path / "emptydir").mkdir()
    pkg = tmp_path / "realpkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")

    assert deps_check._scan_dir_for_modules(tmp_path) == {"realmod", "realpkg"}


def test_local_top_level_names_includes_a_declared_project_name_even_if_already_scanned(
    tmp_path: Path,
) -> None:
    """El propio paquete puede aparecer tanto en el scan del directorio como
    en el nombre declarado en pyproject.toml - la union tiene que conservarlo
    en cualquier caso, no cancelarlo por estar en ambos conjuntos."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "samepkg"\nversion = "0.0.1"\n')
    pkg = tmp_path / "samepkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")

    assert "samepkg" in deps_check._local_top_level_names(tmp_path)


def test_local_top_level_names_root_and_src_overlap_is_not_cancelled(tmp_path: Path) -> None:
    """El mismo paquete puede aparecer tanto en la raiz como en src/ (ej. un
    stub de compatibilidad) - la union entre ambos scans tiene que
    conservarlo, no cancelarlo por estar presente en los dos."""
    root_pkg = tmp_path / "dualpkg"
    root_pkg.mkdir()
    (root_pkg / "__init__.py").write_text("")
    src_pkg = tmp_path / "src" / "dualpkg"
    src_pkg.mkdir(parents=True)
    (src_pkg / "__init__.py").write_text("")

    assert "dualpkg" in deps_check._local_top_level_names(tmp_path)


def test_local_top_level_names_src_and_declared_name_overlap_is_not_cancelled(
    tmp_path: Path,
) -> None:
    """El paquete solo existe bajo src/ (sin equivalente en la raiz) y
    coincide con el nombre declarado en pyproject.toml - tiene que seguir
    apareciendo, no cancelarse por estar en ambos conjuntos."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "srcpkg"\nversion = "0.0.1"\n')
    src_pkg = tmp_path / "src" / "srcpkg"
    src_pkg.mkdir(parents=True)
    (src_pkg / "__init__.py").write_text("")

    assert "srcpkg" in deps_check._local_top_level_names(tmp_path)


def test_transitive_pins_ignores_a_line_without_a_via_comment(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("click>=8.0\n")

    assert deps_check._transitive_pins(tmp_path) == set()


def test_verify_reports_pip_audit_timeout_evidence_on_line_zero(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("click>=8.0\n")
    (tmp_path / "app.py").write_text("import click\n\nclick.echo('hi')\n")

    with patch(
        "auditor.verifiers.deps_check.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="pip-audit", timeout=120),
    ):
        result = deps_check.verify(RepoContext.from_path(tmp_path))

    timeout_evidence = next(e for e in result.evidence if e.file == "pip-audit")
    assert timeout_evidence.line == 0


def test_verify_reports_a_real_vulnerability_from_a_mocked_pip_audit_result(
    tmp_path: Path,
) -> None:
    (tmp_path / "requirements.txt").write_text("badpkg==1.0\ngoodpkg==2.0\n")
    (tmp_path / "app.py").write_text("import badpkg\nimport goodpkg\n")
    fake_results = [
        {"name": "badpkg", "version": "1.0", "vulns": []},
        {
            "name": "goodpkg",
            "version": "2.0",
            "vulns": [{"id": "GHSA-fake-1234"}],
        },
    ]

    with patch(
        "auditor.verifiers.deps_check._run_pip_audit", return_value=fake_results
    ):
        result = deps_check.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.NO_SOSTENIBLE
    assert any("goodpkg" in e.note and "GHSA-fake-1234" in e.note for e in result.evidence)
    assert not any("badpkg" in e.note and "vulnerabilidad" in e.note for e in result.evidence)


def test_verify_reports_undeclared_import_that_has_no_known_package_mapping(
    tmp_path: Path,
) -> None:
    """`sklearn` tiene mapeo conocido a scikit-learn, pero si scikit-learn
    nunca se declara en ningun lado el resultado tiene que ser el mismo
    NO_SOSTENIBLE generico que cualquier otro import sin declarar - no el
    branch de "mapeo conocido, no es un fallo real"."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\nversion = "0.0.1"\ndependencies = []\n'
    )
    (tmp_path / "app.py").write_text("import sklearn\n\nsklearn.__version__\n")

    with patch("auditor.verifiers.deps_check._run_pip_audit", return_value=[]):
        result = deps_check.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.NO_SOSTENIBLE
    assert any(
        "sklearn" in e.note and "no esta declarado" in e.note for e in result.evidence
    )
    assert not any("mapeo conocido" in e.note for e in result.evidence)


def test_verify_stdlib_import_is_skipped_but_a_later_real_undeclared_import_is_still_reported(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\nversion = "0.0.1"\ndependencies = []\n'
    )
    (tmp_path / "app.py").write_text("import json\nimport zzz_undeclared_thing\n")

    with patch("auditor.verifiers.deps_check._run_pip_audit", return_value=[]):
        result = deps_check.verify(RepoContext.from_path(tmp_path))

    assert any("zzz_undeclared_thing" in e.note for e in result.evidence)


def test_verify_does_not_flag_scikit_learn_as_unused_when_the_mapped_import_overlaps(
    tmp_path: Path,
) -> None:
    """`import sklearn` normaliza a 'sklearn', y su mapeo conocido normaliza a
    'scikit_learn'. Si el codigo TAMBIEN importa algo que normaliza al mismo
    nombre que el mapeo ('scikit_learn'), el set de "usados efectivamente"
    tiene overlap con el set del mapeo - la union tiene que conservar
    'scikit_learn' en ambos casos, no cancelarlo."""
    (tmp_path / "requirements.txt").write_text("scikit-learn==1.5.0\n")
    (tmp_path / "app.py").write_text("import sklearn\nimport scikit_learn\n")

    with patch("auditor.verifiers.deps_check._run_pip_audit", return_value=[]):
        result = deps_check.verify(RepoContext.from_path(tmp_path))

    assert not any(
        "scikit_learn" in e.note and "no se usa" in e.note for e in result.evidence
    )


def test_verify_does_not_flag_a_cli_tool_declared_with_a_via_comment_as_unused(
    tmp_path: Path,
) -> None:
    """ruff ya esta en _CLI_ONLY_PACKAGES; si ADEMAS aparece con un
    comentario `# via` (transitive pin), el set de "no reportables" tiene
    overlap consigo mismo entre las dos fuentes - la union tiene que seguir
    conteniendolo, no cancelarlo."""
    (tmp_path / "requirements.txt").write_text("ruff==0.6.0          # via somepkg\n")
    (tmp_path / "app.py").write_text("print('hola')\n")

    with patch("auditor.verifiers.deps_check._run_pip_audit", return_value=[]):
        result = deps_check.verify(RepoContext.from_path(tmp_path))

    assert not any("ruff" in e.note and "no se usa" in e.note for e in result.evidence)


def test_verify_skips_a_no_reportable_unused_dep_but_still_reports_the_next_one(
    tmp_path: Path,
) -> None:
    (tmp_path / "requirements.txt").write_text("ruff>=0.6\nunused_real_dep==1.0\n")
    (tmp_path / "app.py").write_text("print('hola')\n")

    with patch("auditor.verifiers.deps_check._run_pip_audit", return_value=[]):
        result = deps_check.verify(RepoContext.from_path(tmp_path))

    assert any(
        "unused_real_dep" in e.note and "no se usa" in e.note for e in result.evidence
    )

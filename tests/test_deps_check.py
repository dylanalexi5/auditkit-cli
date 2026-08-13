import subprocess
from pathlib import Path
from unittest.mock import patch

from auditor.core.models import Verdict
from auditor.core.repo_context import RepoContext
from auditor.verifiers import deps_check


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

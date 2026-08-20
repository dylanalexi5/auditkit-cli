import json
from pathlib import Path

import pytest

from auditor.core.models import Verdict
from auditor.core.repo_context import RepoContext
from auditor.verifiers import secrets


def test_verify_detects_known_secret(tmp_path: Path) -> None:
    (tmp_path / "config.py").write_text('AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n')

    result = secrets.verify(RepoContext(path=tmp_path))

    assert result.verdict == Verdict.NO_SOSTENIBLE
    assert any(
        e.file == "config.py" and e.line == 1 and "AWS" in e.note
        for e in result.evidence
    )


def test_verify_clean_repo_is_aprobado(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def main() -> None:\n    pass\n")

    result = secrets.verify(RepoContext(path=tmp_path))

    assert result.verdict == Verdict.APROBADO
    assert result.evidence == []


def test_verify_ignores_pre_commit_config_version_hashes(tmp_path: Path) -> None:
    (tmp_path / ".pre-commit-config.yaml").write_text(
        "repos:\n"
        "  - repo: https://github.com/astral-sh/ruff-pre-commit\n"
        "    rev: c60c980e561ed3e73101667fe8365c609d19a438  # frozen: v0.15.9\n"
        "    hooks:\n"
        "      - id: ruff-check\n"
        "  - repo: https://github.com/pre-commit/pre-commit-hooks\n"
        "    rev: 3e8a8703264a2f4a69428a0aa4dcb512790b2c8c  # frozen: v6.0.0\n"
        "    hooks:\n"
        "      - id: trailing-whitespace\n"
    )

    result = secrets.verify(RepoContext(path=tmp_path))

    assert result.verdict == Verdict.APROBADO
    assert result.evidence == []


def test_verify_ignores_notebook_metadata_but_detects_secret_in_cell(
    tmp_path: Path,
) -> None:
    notebook = {
        "cells": [
            {
                "cell_type": "code",
                "metadata": {"_uuid": "051d70d956493feee0c6d64651c6a088724dca2a"},
                "source": ["AWS_KEY = \"AKIAIOSFODNN7EXAMPLE\"\n"],
                "outputs": [],
                "execution_count": None,
            }
        ],
        "metadata": {
            "interpreter": {
                "hash": "e758f3098b5b55f4d87fe30bbdc1367f20f246b483f96267ee70e6c40cb185d8"
            },
            "kernelspec": {"display_name": "Python 3", "name": "python3"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    (tmp_path / "notebook.ipynb").write_text(json.dumps(notebook))

    result = secrets.verify(RepoContext(path=tmp_path))

    assert result.verdict == Verdict.NO_SOSTENIBLE
    assert len(result.evidence) == 1
    assert result.evidence[0].file == "notebook.ipynb"
    assert result.evidence[0].line == 1
    assert "AWS" in result.evidence[0].note


def test_verify_notebook_secret_reports_the_real_cell_index(tmp_path: Path) -> None:
    notebook = {
        "cells": [
            {"cell_type": "markdown", "metadata": {}, "source": ["intro\n"]},
            {"cell_type": "code", "metadata": {}, "source": ["x = 1\n"], "outputs": []},
            {
                "cell_type": "code",
                "metadata": {},
                "source": ['AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n'],
                "outputs": [],
            },
        ]
    }
    (tmp_path / "notebook.ipynb").write_text(json.dumps(notebook))

    result = secrets.verify(RepoContext(path=tmp_path))

    assert len(result.evidence) == 1
    assert result.evidence[0].line == 3


def test_verify_notebook_handles_string_source_not_just_list(tmp_path: Path) -> None:
    """nbformat permite `source` como lista de lineas O como un string suelto
    - las dos formas son validas."""
    notebook = {
        "cells": [
            {
                "cell_type": "code",
                "metadata": {},
                "source": 'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n',
                "outputs": [],
            }
        ]
    }
    (tmp_path / "notebook.ipynb").write_text(json.dumps(notebook))

    result = secrets.verify(RepoContext(path=tmp_path))

    assert result.verdict == Verdict.NO_SOSTENIBLE
    assert "AWS" in result.evidence[0].note


def test_verify_notebook_skips_empty_cells_and_finds_secret_later(tmp_path: Path) -> None:
    notebook = {
        "cells": [
            {"cell_type": "code", "metadata": {}, "source": [], "outputs": []},
            {"cell_type": "code", "metadata": {}, "source": ["   \n"], "outputs": []},
            {
                "cell_type": "code",
                "metadata": {},
                "source": ['AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n'],
                "outputs": [],
            },
        ]
    }
    (tmp_path / "notebook.ipynb").write_text(json.dumps(notebook))

    result = secrets.verify(RepoContext(path=tmp_path))

    assert len(result.evidence) == 1
    assert result.evidence[0].line == 3


def test_verify_survives_malformed_notebook_json(tmp_path: Path) -> None:
    (tmp_path / "broken.ipynb").write_text("{not valid json")
    (tmp_path / "app.py").write_text("def main() -> None:\n    pass\n")

    result = secrets.verify(RepoContext(path=tmp_path))

    assert result.verdict == Verdict.APROBADO
    assert result.evidence == []


def test_verify_continues_scanning_notebooks_after_an_excluded_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El orden real de Path.rglob() no esta garantizado - se fuerza a mano
    para que el excluido se visite ANTES que el real. Si _scan_notebooks
    usara `break` en vez de `continue` al toparse con el excluido, esta es
    la unica forma de que el test lo note de manera confiable."""
    cache_dir = tmp_path / ".pytest_cache"
    cache_dir.mkdir()
    excluded = cache_dir / "generated.ipynb"
    excluded.write_text(
        json.dumps({"cells": [{"cell_type": "code", "source": ["import os\n"]}]})
    )
    real = tmp_path / "real.ipynb"
    real.write_text(
        json.dumps(
            {
                "cells": [
                    {
                        "cell_type": "code",
                        "source": ['AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n'],
                    }
                ]
            }
        )
    )

    original_rglob = Path.rglob

    def ordered_rglob(self: Path, pattern: str):
        if self == tmp_path and pattern == "*.ipynb":
            return iter([excluded, real])
        return original_rglob(self, pattern)

    monkeypatch.setattr(Path, "rglob", ordered_rglob)

    result = secrets.verify(RepoContext(path=tmp_path))

    assert result.verdict == Verdict.NO_SOSTENIBLE
    assert any(e.file == "real.ipynb" for e in result.evidence)


def test_verify_ignores_artifacts_generated_by_the_auditor(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("def main() -> None:\n    pass\n")
    cache = tmp_path / ".pytest_cache"
    cache.mkdir()
    (cache / "CACHEDIR.TAG").write_text(
        "Signature: 8a477f597d28d172789f06886806bc55\n"
    )
    pycache = tmp_path / "__pycache__"
    pycache.mkdir()
    (pycache / "app.cpython-311.pyc").write_text(
        'TOKEN = "AKIAIOSFODNN7EXAMPLE"\n'
    )

    result = secrets.verify(RepoContext(path=tmp_path))

    assert result.verdict == Verdict.APROBADO
    assert result.evidence == []


# --- .secrets.baseline del repo auditado ----------------------------------
# El baseline es la convencion de detect-secrets para "esto ya lo miramos y
# lo aceptamos". Sin leerlo, cualquier repo con fixtures de test que llevan
# secretos falsos a proposito —este mismo, sin ir mas lejos— sale
# NO_SOSTENIBLE por su propio andamiaje de tests.


def _baseline(tmp_path: Path, *entradas: tuple[str, str]) -> None:
    """Escribe un `.secrets.baseline` con formato de detect-secrets."""
    from detect_secrets.core.potential_secret import PotentialSecret

    resultados: dict[str, list[dict]] = {}
    for archivo, valor in entradas:
        resultados.setdefault(archivo, []).append(
            {
                "type": "AWS Access Key",
                "filename": archivo,
                "hashed_secret": PotentialSecret.hash_secret(valor),
                "is_verified": False,
                "line_number": 1,
            }
        )
    (tmp_path / ".secrets.baseline").write_text(
        json.dumps({"version": "1.5.0", "results": resultados}), encoding="utf-8"
    )


def test_un_hallazgo_registrado_en_el_baseline_no_reprueba(tmp_path: Path) -> None:
    (tmp_path / "config.py").write_text('AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n')
    _baseline(tmp_path, ("config.py", "AKIAIOSFODNN7EXAMPLE"))

    result = secrets.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.APROBADO_CON_OBSERVACIONES
    assert any("config.py" in e.file for e in result.evidence)


def test_el_hallazgo_registrado_sigue_en_el_reporte_y_dice_por_que(
    tmp_path: Path,
) -> None:
    """Registrado no es invisible. El repo controla su propio baseline, asi
    que dejar de mostrar el hallazgo seria dejar que el auditado apague al
    auditor."""
    (tmp_path / "config.py").write_text('AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n')
    _baseline(tmp_path, ("config.py", "AKIAIOSFODNN7EXAMPLE"))

    result = secrets.verify(RepoContext.from_path(tmp_path))

    assert len(result.evidence) == 1
    assert ".secrets.baseline" in result.evidence[0].note
    assert "AWS Access Key" in result.evidence[0].note


def test_el_baseline_no_puede_llevar_el_veredicto_a_aprobado(tmp_path: Path) -> None:
    """Mismo techo que el agente de triage: puede bajar el ruido, no puede
    declarar inocencia."""
    (tmp_path / "config.py").write_text('AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n')
    _baseline(tmp_path, ("config.py", "AKIAIOSFODNN7EXAMPLE"))

    result = secrets.verify(RepoContext.from_path(tmp_path))

    assert result.verdict != Verdict.APROBADO


def test_un_hallazgo_que_no_esta_en_el_baseline_sigue_reprobando(
    tmp_path: Path,
) -> None:
    """El baseline aplica al secreto exacto que registra, no al archivo: si
    alguien agrega una credencial nueva al lado de una ya aceptada, el
    veredicto tiene que volver a NO_SOSTENIBLE."""
    (tmp_path / "config.py").write_text(
        'VIEJA = "AKIAIOSFODNN7EXAMPLE"\nNUEVA = "AKIAI44QH8DHBEXAMPLE"\n'
    )
    _baseline(tmp_path, ("config.py", "AKIAIOSFODNN7EXAMPLE"))

    result = secrets.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.NO_SOSTENIBLE


def test_un_baseline_ilegible_se_ignora_y_no_tumba_el_verificador(
    tmp_path: Path,
) -> None:
    """Mismo criterio que el resto del proyecto ante un archivo ajeno roto:
    se saltea. Y se saltea hacia el lado SEGURO — sin allowlist, el hallazgo
    cuenta."""
    (tmp_path / "config.py").write_text('AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n')
    (tmp_path / ".secrets.baseline").write_text("{no es json", encoding="utf-8")

    result = secrets.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.NO_SOSTENIBLE


def test_un_baseline_de_otro_archivo_no_tapa_el_hallazgo(tmp_path: Path) -> None:
    """El mismo secreto registrado para OTRO archivo no vale: la clave es el
    par (archivo, hash), no el hash suelto."""
    (tmp_path / "config.py").write_text('AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n')
    _baseline(tmp_path, ("otro.py", "AKIAIOSFODNN7EXAMPLE"))

    result = secrets.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.NO_SOSTENIBLE


def test_el_baseline_encuentra_el_hallazgo_en_un_subdirectorio(tmp_path: Path) -> None:
    """detect-secrets escribe la ruta con el separador del sistema —barra
    invertida en Windows— y la lee igual en Linux. Se normaliza a `/` en los
    dos lados, si no el baseline solo funciona en el SO donde se genero."""
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "config.py").write_text('AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n')
    _baseline(tmp_path, ("sub/config.py", "AKIAIOSFODNN7EXAMPLE"))

    result = secrets.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.APROBADO_CON_OBSERVACIONES


def test_sin_baseline_el_comportamiento_no_cambia(tmp_path: Path) -> None:
    (tmp_path / "config.py").write_text('AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n')

    result = secrets.verify(RepoContext.from_path(tmp_path))

    assert result.verdict == Verdict.NO_SOSTENIBLE
    assert ".secrets.baseline" not in result.evidence[0].note

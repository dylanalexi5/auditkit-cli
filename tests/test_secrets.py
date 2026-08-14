import json
from pathlib import Path

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
    assert "AWS" in result.evidence[0].note


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

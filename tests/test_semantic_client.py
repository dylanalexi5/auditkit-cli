from pathlib import Path

import pytest

from auditor.core import semantic_client

_MENSAJE_ESPERADO = (
    "Falta GROQ_API_KEY en .env — el verificador semántico no puede correr sin ella"
)


def test_falta_la_clave_falla_con_mensaje_claro(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    with pytest.raises(semantic_client.MissingApiKeyError) as exc_info:
        semantic_client.get_client(dotenv_path=tmp_path / ".env")

    assert str(exc_info.value) == _MENSAJE_ESPERADO


def test_clave_vacia_se_trata_como_ausente(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "   ")

    with pytest.raises(semantic_client.MissingApiKeyError) as exc_info:
        semantic_client.get_client(dotenv_path=tmp_path / ".env")

    assert str(exc_info.value) == _MENSAJE_ESPERADO


def test_lee_la_clave_del_entorno(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "gsk_clave_del_entorno")

    client = semantic_client.get_client(dotenv_path=tmp_path / ".env")

    assert client.api_key == "gsk_clave_del_entorno"


def test_lee_la_clave_desde_el_archivo_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text('# comentario\nGROQ_API_KEY="gsk_clave_del_archivo"\n')

    client = semantic_client.get_client(dotenv_path=env_file)

    assert client.api_key == "gsk_clave_del_archivo"


def test_el_entorno_gana_sobre_el_archivo_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "gsk_del_entorno")
    env_file = tmp_path / ".env"
    env_file.write_text("GROQ_API_KEY=gsk_del_archivo\n")

    client = semantic_client.get_client(dotenv_path=env_file)

    assert client.api_key == "gsk_del_entorno"


def test_leer_el_env_no_muta_el_entorno_del_proceso(tmp_path: Path, monkeypatch) -> None:
    """El .env se lee, no se exporta: cargar una clave para un cliente no debe
    dejarla colgada en os.environ para el resto del proceso."""
    import os

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("GROQ_API_KEY=gsk_del_archivo\n")

    semantic_client.get_client(dotenv_path=env_file)

    assert "GROQ_API_KEY" not in os.environ

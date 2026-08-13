"""Conexión con la API de Groq para el verificador semántico.

Todavía no hay verificador semántico: esto es solo el cliente, listo para que
funcione apenas haya una GROQ_API_KEY real en `.env`. La clave se busca primero
en el entorno y después en el archivo `.env` de la raíz del repo.
"""

import os
from pathlib import Path

from groq import Groq

_ENV_VAR = "GROQ_API_KEY"
_MISSING_KEY_MESSAGE = (
    "Falta GROQ_API_KEY en .env — el verificador semántico no puede correr sin ella"
)
_DEFAULT_DOTENV = Path(__file__).resolve().parents[2] / ".env"


class MissingApiKeyError(RuntimeError):
    """Falta la credencial de Groq. Se distingue de cualquier error de red o de
    la API para que el mensaje al usuario diga qué hacer y no un stacktrace del
    SDK."""


def _read_dotenv(path: Path) -> dict[str, str]:
    """Parsea KEY=VALUE de un .env y devuelve el mapeo, sin tocar os.environ.

    Se hace a mano en vez de sumar python-dotenv: son pocas líneas y el ADR fija
    no agregar dependencias que no compren algo real. No exportar al entorno del
    proceso es deliberado — leer una credencial para construir un cliente no
    debería dejarla colgada para todo lo que corra después.
    """
    if not path.is_file():
        return {}

    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip().removeprefix("export ").strip()
        if key:
            values[key] = value.strip().strip('"').strip("'")
    return values


def get_api_key(dotenv_path: Path | None = None) -> str:
    """Devuelve la GROQ_API_KEY, o falla con un mensaje accionable si no está.

    El entorno gana sobre el `.env`: así se puede sobrescribir la clave en CI o
    en una corrida puntual sin editar el archivo.
    """
    key = os.environ.get(_ENV_VAR, "").strip()
    if not key:
        source = _DEFAULT_DOTENV if dotenv_path is None else dotenv_path
        key = _read_dotenv(source).get(_ENV_VAR, "").strip()
    if not key:
        raise MissingApiKeyError(_MISSING_KEY_MESSAGE)
    return key


def get_client(dotenv_path: Path | None = None) -> Groq:
    """Cliente de Groq listo para usar."""
    return Groq(api_key=get_api_key(dotenv_path))

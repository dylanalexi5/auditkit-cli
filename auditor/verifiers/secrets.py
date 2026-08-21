import itertools
import json
import tempfile
from pathlib import Path

from detect_secrets.core import baseline
from detect_secrets.core.secrets_collection import SecretsCollection
from detect_secrets.settings import default_settings

from auditor.core.models import Evidence, Verdict, VerifierResult
from auditor.core.repo_context import RepoContext

# Artefactos generados: por las herramientas del repo o por el propio auditor
# al correr build_check. No son codigo del repo auditado, asi que no son parte
# de lo que se audita. Sin este filtro, build_check deja .pytest_cache/ en el
# clon y secrets marca su CACHEDIR.TAG como "Hex High Entropy String" - el
# veredicto pasaba a depender del orden en que corren los verificadores.
_GENERATED_DIRS = frozenset(
    {
        ".pytest_cache",
        "__pycache__",
        ".ruff_cache",
        ".mypy_cache",
        ".tox",
        ".nox",
        ".eggs",
        "htmlcov",
    }
)
_GENERATED_DIR_SUFFIXES = (".egg-info",)

# .pre-commit-config.yaml declara sus hooks con `rev: <sha git de 40 hex>`
# ("frozen" a un commit puntual) - son hashes de version, no secretos. Un repo
# real con hooks pineados (pallets/click, ej.) dispara "Hex High Entropy
# String" en cada rev. Se excluye por nombre de archivo, no por heuristica -
# ver ADR 0001, seccion de limitaciones.
#
# `.secrets.baseline` va por la misma puerta y por una razon mas fuerte: su
# contenido son hashes POR CONSTRUCCION. Escanearlo hace que el archivo que
# existe para bajar falsos positivos genere falsos positivos propios.
_EXCLUDED_FILENAMES = frozenset({".pre-commit-config.yaml", ".secrets.baseline"})

# La convencion de detect-secrets para "esto ya lo miramos y lo aceptamos".
# Sin leerlo, cualquier repo con fixtures de test que llevan secretos falsos a
# proposito —este mismo, sin ir mas lejos— sale NO_SOSTENIBLE por su propio
# andamiaje de tests.
_BASELINE_FILENAME = ".secrets.baseline"
_NOTA_REGISTRADO = "registrado en .secrets.baseline del repo"

# tests/certs/, test/certs/ - convencion para certificados TLS de prueba
# generados a proposito (psf/requests: tests/certs/expired/ca/ca-private.key,
# tests/certs/valid/server/server.key, para testear el cliente HTTP contra
# certificados invalidos/expirados). No se ocultan -- bajarian la señal real
# si alguien deja una clave de verdad en esa misma ruta -- se marcan para
# revision manual y no cuentan solas para NO_SOSTENIBLE.
_TEST_DIR_NAMES = frozenset({"tests", "test"})
_CERT_FIXTURE_DIR_NAMES = frozenset({"certs", "cert"})
_NOTA_FIXTURE_CERT = "posible fixture de certificados de test - revisar manualmente"


def _es_fixture_de_certificados(filename: str) -> bool:
    parts = [p.lower() for p in Path(filename).parts]
    return any(
        a in _TEST_DIR_NAMES and b in _CERT_FIXTURE_DIR_NAMES
        for a, b in itertools.pairwise(parts)
    )


def _ruta_normalizada(nombre: str) -> str:
    """Misma forma en los dos lados de la comparacion.

    detect-secrets escribe la ruta con el separador del sistema
    (`sub\\otro.py` en Windows) y a veces con un `./` adelante segun como se
    haya invocado. Sin normalizar, un baseline generado en Windows no sirve
    en Linux y viceversa.
    """
    return nombre.replace("\\", "/").removeprefix("./")


def _registrados(root: Path) -> frozenset[tuple[str, str]]:
    """Pares `(archivo, hash)` que el repo ya registro y acepto.

    Un baseline ilegible se ignora, y se ignora hacia el lado SEGURO: sin
    allowlist, el hallazgo cuenta. Mismo criterio que el resto del proyecto
    ante un archivo ajeno roto — no tumbar el verificador— pero acá el
    sentido de la degradacion importa mas que el hecho de degradar.
    """
    archivo = root / _BASELINE_FILENAME
    if not archivo.is_file():
        return frozenset()
    try:
        datos = json.loads(archivo.read_text(encoding="utf-8", errors="ignore"))
    except json.JSONDecodeError:
        return frozenset()

    resultados = datos.get("results") if isinstance(datos, dict) else None
    if not isinstance(resultados, dict):
        return frozenset()

    return frozenset(
        (_ruta_normalizada(nombre), item["hashed_secret"])
        for nombre, items in resultados.items()
        if isinstance(items, list)
        for item in items
        if isinstance(item, dict) and isinstance(item.get("hashed_secret"), str)
    )


def _is_excluded(filename: str) -> bool:
    parts = Path(filename).parts
    if any(part in _GENERATED_DIRS or part.endswith(_GENERATED_DIR_SUFFIXES) for part in parts):
        return True
    return Path(filename).name in _EXCLUDED_FILENAMES


def _notebook_cell_sources(notebook_path: Path) -> list[str]:
    """Fuente de cada celda de un .ipynb, en orden. Nunca la metadata:
    detect-secrets no tiene plugin para notebooks (confirmado - no hay nada
    ipynb/jupyter en su paquete), y la metadata de Jupyter esta llena de
    strings hex que parecen secretos sin serlo: hash del interprete
    (`metadata.interpreter.hash`), uuid de celda estilo Kaggle
    (`cell.metadata._uuid`), id de celda (nbformat 4.5+). Ninguno lo escribio
    un humano - son bookkeeping del propio Jupyter. Un secreto de verdad,
    pegado a mano en una celda de codigo, vive en `source` - eso si se
    escanea."""
    try:
        data = json.loads(notebook_path.read_text(encoding="utf-8", errors="ignore"))
    except json.JSONDecodeError:
        return []

    sources = []
    for cell in data.get("cells", []):
        source = cell.get("source", "")
        text = "".join(source) if isinstance(source, list) else str(source)
        sources.append(text)
    return sources


def _scan_notebooks(root: Path) -> list[Evidence]:
    evidence: list[Evidence] = []
    for notebook_path in root.rglob("*.ipynb"):
        relative = str(notebook_path.relative_to(root))
        if _is_excluded(relative):
            continue

        for cell_index, source in enumerate(_notebook_cell_sources(notebook_path), start=1):
            if not source.strip():
                continue

            with tempfile.NamedTemporaryFile(
                "w", suffix=".py", delete=False, encoding="utf-8"
            ) as tmp_file:
                tmp_file.write(source)
                tmp_path = Path(tmp_file.name)

            try:
                collection = SecretsCollection()
                with default_settings():
                    collection.scan_file(str(tmp_path))
                for secrets_in_file in collection.data.values():
                    for secret in secrets_in_file:
                        evidence.append(
                            Evidence(file=relative, line=cell_index, note=secret.type)
                        )
            finally:
                tmp_path.unlink(missing_ok=True)

    return evidence


def verify(ctx: RepoContext) -> VerifierResult:
    with default_settings():
        found = baseline.create(str(ctx.path), should_scan_all_files=True, root=str(ctx.path))

    registrados = _registrados(ctx.path)
    evidence: list[Evidence] = []
    sin_registrar = 0
    for filename, secrets_in_file in found.data.items():
        if _is_excluded(filename) or filename.endswith(".ipynb"):
            continue
        for secret in secrets_in_file:
            esta_registrado = (
                _ruta_normalizada(filename),
                secret.secret_hash,
            ) in registrados
            # Registrado no es invisible: el hallazgo sigue en el reporte, con
            # su ubicacion y el motivo por el que no cuenta. El repo auditado
            # controla su propio baseline, asi que dejar de mostrarlo seria
            # dejar que el auditado apague al auditor.
            es_fixture_cert = _es_fixture_de_certificados(filename)
            if esta_registrado:
                nota = f"{secret.type} - {_NOTA_REGISTRADO}"
            elif es_fixture_cert:
                nota = f"{secret.type} - {_NOTA_FIXTURE_CERT}"
            else:
                nota = secret.type
            evidence.append(Evidence(file=filename, line=secret.line_number, note=nota))
            sin_registrar += not esta_registrado and not es_fixture_cert

    # Los notebooks no pasan por el baseline: sus hallazgos salen de un
    # archivo temporal por celda, asi que no hay ruta estable que registrar
    # (ver el pendiente de `line=cell_index` anotado en CLAUDE.md).
    notebooks = _scan_notebooks(ctx.path)
    evidence.extend(notebooks)
    sin_registrar += len(notebooks)

    if sin_registrar:
        verdict = Verdict.NO_SOSTENIBLE
    elif evidence:
        # Mismo techo que el agente de triage (ADR 0003): puede bajar el
        # ruido, no puede declarar inocencia.
        verdict = Verdict.APROBADO_CON_OBSERVACIONES
    else:
        verdict = Verdict.APROBADO
    return VerifierResult(verdict=verdict, evidence=evidence)

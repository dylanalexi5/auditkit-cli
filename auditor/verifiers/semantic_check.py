import json
import re
from pathlib import Path

import groq

from auditor.core.models import Evidence, Verdict, VerifierResult
from auditor.core.repo_context import RepoContext, declared_project_names
from auditor.core.semantic_client import MissingApiKeyError, get_client

_MODEL = "llama-3.3-70b-versatile"
_TIMEOUT_SECONDS = 30
_README_NAMES = ("README.md", "README.rst", "README.txt", "README")
_KEYWORD = re.compile(r"[a-záéíóúñ0-9]{4,}")
_STOPWORDS = frozenset(
    {
        "para", "esta", "este", "esto", "ested", "con", "que", "los", "las",
        "una", "uno", "por", "del", "the", "and", "this", "that", "with",
        "from", "have", "has", "was", "are", "your", "you",
        # Vocabulario generico de las notas de evidencia que escribimos
        # nosotros mismos - compartido entre los 4 verificadores. Sin esto,
        # CUALQUIER afirmacion matchea CUALQUIER evidencia con solo compartir
        # una de estas palabras ("version", "codigo"), sin relacion tematica
        # real - la misma evidencia le pegaba a todas las afirmaciones.
        "declarado", "declara", "declaradas", "declarados", "importa",
        "import", "imports", "codigo", "code", "hay", "real", "version",
        "versiones", "archivo", "archivos", "file", "files", "modulo",
        "modulos", "module", "requirements", "toml", "txt", "pyproject",
        "evidencia", "fallo", "verificado", "repo", "proyecto", "project",
    }
)
_UNAVAILABLE_NOTE_TEMPLATE = "verificador semántico saltado: {reason}"

_SYSTEM_PROMPT = (
    "Extraés afirmaciones verificables de un README de un repositorio de "
    "software. NO juzgás si son ciertas o falsas - eso lo hace otro sistema. "
    "Identificás afirmaciones concretas (features, calidad, cobertura de "
    "tests, estado del proyecto, seguridad, rendimiento) y citás el texto "
    "exacto del README de donde salen.\n\n"
    'Devolvé SOLO un JSON con esta forma exacta: '
    '{"afirmaciones": [{"afirmacion": "resumen corto", '
    '"cita_textual_del_readme": "texto exacto copiado del README"}]}\n'
    'Si no hay afirmaciones verificables, devolvé {"afirmaciones": []}.'
)


def _find_readme(path: Path) -> Path | None:
    for name in _README_NAMES:
        candidate = path / name
        if candidate.is_file():
            return candidate
    return None


def _skipped(reason: str) -> VerifierResult:
    return VerifierResult(
        verdict=Verdict.APROBADO_CON_OBSERVACIONES,
        evidence=[
            Evidence(
                file="(sin verificar)",
                line=0,
                note=_UNAVAILABLE_NOTE_TEMPLATE.format(reason=reason),
            )
        ],
    )


def _extract_claims(client: groq.Groq, readme_text: str) -> list[dict] | None:
    """None = no se pudo obtener una lista de afirmaciones valida - timeout,
    error de API, o la respuesta no cumple el schema esperado. Nunca se
    reintenta: temperature=0 hace que un JSON invalido una vez no vaya a
    arreglarse solo en un segundo intento con el mismo prompt."""
    try:
        completion = client.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": readme_text},
            ],
            temperature=0,
            response_format={"type": "json_object"},
            timeout=_TIMEOUT_SECONDS,
        )
    except groq.APIError:
        return None

    try:
        payload = json.loads(completion.choices[0].message.content)
    except (json.JSONDecodeError, IndexError, AttributeError, TypeError):
        return None

    claims = payload.get("afirmaciones") if isinstance(payload, dict) else None
    if not isinstance(claims, list):
        return None

    return [
        claim
        for claim in claims
        if isinstance(claim, dict)
        and isinstance(claim.get("afirmacion"), str)
        and isinstance(claim.get("cita_textual_del_readme"), str)
    ]


def _keywords(text: str, exclude: frozenset[str] = frozenset()) -> set[str]:
    return {
        token
        for token in _KEYWORD.findall(text.lower())
        if token not in _STOPWORDS and token not in exclude
    }


def _project_name_tokens(path: Path) -> frozenset[str]:
    """El nombre del propio proyecto ('black', 'click'...) tokenizado igual
    que cualquier otro texto. Se excluye del cruce por la misma razon que el
    vocabulario generico: aparece en casi cualquier afirmacion sobre el
    repo ("_Black_ reformats...", "_Black_ is PEP 8...") y en evidencia que
    no tiene nada que ver (ej. un modulo interno '_black_version' sin
    declarar) - sin excluirlo, TODO matchea con TODO por compartir el
    nombre del proyecto."""
    tokens: set[str] = set()
    for name in declared_project_names(path):
        tokens.update(_KEYWORD.findall(name))
    return frozenset(tokens)


def _locate_quote(readme_text: str, quote: str) -> int:
    index = readme_text.find(quote)
    if index == -1:
        return 1
    return readme_text.count("\n", 0, index) + 1


def _find_contradicting_evidence(
    claim_keywords: set[str],
    other_results: dict[str, VerifierResult],
    exclude: frozenset[str],
) -> Evidence | None:
    if not claim_keywords:
        return None
    for result in other_results.values():
        for item in result.evidence:
            if claim_keywords & _keywords(item.note, exclude):
                return item
    return None


def verify(ctx: RepoContext, other_results: dict[str, VerifierResult]) -> VerifierResult:
    readme_path = _find_readme(ctx.path)
    if readme_path is None:
        return VerifierResult(verdict=Verdict.APROBADO, evidence=[])

    try:
        client = get_client()
    except MissingApiKeyError:
        return _skipped("falta GROQ_API_KEY")

    readme_text = readme_path.read_text(encoding="utf-8", errors="ignore")
    claims = _extract_claims(client, readme_text)
    if claims is None:
        return _skipped("la API no respondió o la respuesta no era el JSON esperado")
    if not claims:
        return VerifierResult(verdict=Verdict.APROBADO, evidence=[])

    exclude = _project_name_tokens(ctx.path)
    evidence: list[Evidence] = []
    for claim in claims:
        afirmacion = claim["afirmacion"]
        cita = claim["cita_textual_del_readme"]
        claim_keywords = _keywords(afirmacion, exclude)
        contradicting = _find_contradicting_evidence(claim_keywords, other_results, exclude)
        if contradicting is None:
            continue
        evidence.append(
            Evidence(
                file=readme_path.name,
                line=_locate_quote(readme_text, cita),
                note=(
                    f'README dice "{cita}" pero hay evidencia que lo contradice: '
                    f"{contradicting.file}:{contradicting.line} — {contradicting.note}"
                ),
            )
        )

    verdict = Verdict.APROBADO_CON_OBSERVACIONES if evidence else Verdict.APROBADO
    return VerifierResult(verdict=verdict, evidence=evidence)

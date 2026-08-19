import json
import os
from pathlib import Path

import groq
import pytest

from auditor.core.models import Evidence, Verdict, VerifierResult
from auditor.core.repo_context import RepoContext
from auditor.core.semantic_client import MissingApiKeyError
from auditor.verifiers import semantic_check


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeCompletion:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, content: str | None = None, exc: Exception | None = None) -> None:
        self._content = content
        self._exc = exc
        self.received_kwargs: dict | None = None

    def create(self, **kwargs):
        self.received_kwargs = kwargs
        if self._exc is not None:
            raise self._exc
        return _FakeCompletion(self._content)


class _FakeChat:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, content: str | None = None, exc: Exception | None = None) -> None:
        self.chat = _FakeChat(_FakeCompletions(content=content, exc=exc))


def _claims_payload(*claims: tuple[str, str]) -> str:
    return json.dumps(
        {
            "afirmaciones": [
                {"afirmacion": afirmacion, "cita_textual_del_readme": cita}
                for afirmacion, cita in claims
            ]
        }
    )


def test_verify_no_readme_is_aprobado_without_needing_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*args, **kwargs):
        raise AssertionError("no deberia llamar a get_client sin README")

    monkeypatch.setattr(semantic_check, "get_client", _boom)

    result = semantic_check.verify(RepoContext.from_path(tmp_path), {})

    assert result.verdict == Verdict.APROBADO
    assert result.evidence == []


def test_verify_missing_api_key_is_observaciones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "README.md").write_text("# demo\n\nProduction-ready and fully tested.\n")
    monkeypatch.setattr(
        semantic_check,
        "get_client",
        lambda: (_ for _ in ()).throw(MissingApiKeyError("falta")),
    )

    result = semantic_check.verify(RepoContext.from_path(tmp_path), {})

    assert result.verdict == Verdict.APROBADO_CON_OBSERVACIONES
    assert len(result.evidence) == 1
    assert "GROQ_API_KEY" in result.evidence[0].note
    assert result.evidence[0].file == "(sin verificar)"
    assert result.evidence[0].line == 0


def test_extract_claims_calls_api_with_expected_params() -> None:
    fake_client = _FakeClient(content=_claims_payload())

    claims = semantic_check._extract_claims(fake_client, "un readme cualquiera")

    assert claims == []
    kwargs = fake_client.chat.completions.received_kwargs
    # Valores literales a proposito, no semantic_check._MODEL/_TIMEOUT_SECONDS:
    # comparar contra el mismo atributo que se esta probando es tautologico -
    # si alguien cambia la constante, el assert se mueve con ella y nunca falla.
    assert kwargs["model"] == "qwen/qwen3.6-27b"
    assert kwargs["temperature"] == 0
    assert kwargs["timeout"] == 30
    assert kwargs["response_format"] == {"type": "json_object"}


def test_verify_does_not_match_unrelated_claims_via_project_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reproduce el bug real en psf/black: 'black' (nombre del proyecto)
    aparece en casi cualquier afirmacion sobre si mismo ("_Black_ is...",
    "_Black_ has...") y coincidia con evidencia de un modulo interno sin
    relacion ('_black_version' sin declarar) - la misma evidencia le pegaba
    a TODAS las afirmaciones, sin importar el tema."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "black"\n')
    (tmp_path / "README.md").write_text(
        "# demo\n\n_Black_ is licensed under MIT.\n\n_Black_ has complete test coverage.\n"
    )
    content = _claims_payload(
        ("Black is licensed under MIT", "_Black_ is licensed under MIT."),
        ("Black has complete test coverage", "_Black_ has complete test coverage."),
    )
    monkeypatch.setattr(semantic_check, "get_client", lambda: _FakeClient(content=content))

    other_results = {
        "deps_check": VerifierResult(
            verdict=Verdict.NO_SOSTENIBLE,
            evidence=[
                Evidence(
                    file="pyproject.toml",
                    line=1,
                    note=(
                        "'_black_version' se importa en el codigo pero no esta "
                        "declarado en requirements.txt/pyproject.toml"
                    ),
                )
            ],
        ),
        "readme_check": VerifierResult(
            verdict=Verdict.NO_SOSTENIBLE,
            evidence=[
                Evidence(file="README.md", line=5, note="no hay funciones de test en el repo")
            ],
        ),
    }

    result = semantic_check.verify(RepoContext.from_path(tmp_path), other_results)

    assert result.verdict == Verdict.APROBADO_CON_OBSERVACIONES
    assert len(result.evidence) == 1
    assert "test coverage" in result.evidence[0].note
    assert "MIT" not in result.evidence[0].note


def test_verify_contradicting_evidence_is_observaciones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "README.md").write_text(
        "# demo\n\nThis project has complete test coverage.\n"
    )
    content = _claims_payload(
        ("complete test coverage", "This project has complete test coverage.")
    )
    monkeypatch.setattr(semantic_check, "get_client", lambda: _FakeClient(content=content))

    other_results = {
        "readme_check": VerifierResult(
            verdict=Verdict.NO_SOSTENIBLE,
            evidence=[
                Evidence(
                    file="README.md",
                    line=3,
                    note='README afirma "coverage" pero no hay funciones de test en el repo',
                )
            ],
        )
    }

    result = semantic_check.verify(RepoContext.from_path(tmp_path), other_results)

    assert result.verdict == Verdict.APROBADO_CON_OBSERVACIONES
    assert len(result.evidence) == 1
    assert "test coverage" in result.evidence[0].note
    assert "no hay funciones de test" in result.evidence[0].note
    assert result.evidence[0].file == "README.md"
    assert result.evidence[0].line == 3


def test_verify_claim_without_related_evidence_is_aprobado(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "README.md").write_text("# demo\n\nWritten in idiomatic Python.\n")
    content = _claims_payload(("idiomatic Python", "Written in idiomatic Python."))
    monkeypatch.setattr(semantic_check, "get_client", lambda: _FakeClient(content=content))

    other_results = {
        "secrets": VerifierResult(verdict=Verdict.APROBADO, evidence=[]),
    }

    result = semantic_check.verify(RepoContext.from_path(tmp_path), other_results)

    assert result.verdict == Verdict.APROBADO
    assert result.evidence == []


def test_verify_unrelated_nonempty_evidence_is_not_a_false_contradiction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """other_results trae evidencia real (no vacia) pero de un tema sin
    relacion - el cruce de keywords no debe confundir "hay evidencia" con
    "hay evidencia relacionada"."""
    (tmp_path / "README.md").write_text("# demo\n\nWritten in idiomatic Python.\n")
    content = _claims_payload(("idiomatic Python", "Written in idiomatic Python."))
    monkeypatch.setattr(semantic_check, "get_client", lambda: _FakeClient(content=content))

    other_results = {
        "secrets": VerifierResult(
            verdict=Verdict.NO_SOSTENIBLE,
            evidence=[Evidence(file="config.py", line=4, note="AWS Access Key detectado")],
        )
    }

    result = semantic_check.verify(RepoContext.from_path(tmp_path), other_results)

    assert result.verdict == Verdict.APROBADO
    assert result.evidence == []


def test_verify_filters_out_malformed_individual_claims(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un item de la lista con forma invalida (falta un campo, o no es un
    dict) se descarta solo a el - no invalida el resto de afirmaciones
    bien formadas ni crashea el verificador."""
    (tmp_path / "README.md").write_text("# demo\n\nComplete test coverage.\n")
    payload = json.dumps(
        {
            "afirmaciones": [
                {
                    "afirmacion": "complete test coverage",
                    "cita_textual_del_readme": "Complete test coverage.",
                },
                {"afirmacion": "falta la cita"},
                "no es ni un dict",
                {"afirmacion": 123, "cita_textual_del_readme": "x"},
            ]
        }
    )
    monkeypatch.setattr(semantic_check, "get_client", lambda: _FakeClient(content=payload))

    other_results = {
        "readme_check": VerifierResult(
            verdict=Verdict.NO_SOSTENIBLE,
            evidence=[
                Evidence(
                    file="README.md", line=3, note="no hay funciones de test coverage"
                )
            ],
        )
    }

    result = semantic_check.verify(RepoContext.from_path(tmp_path), other_results)

    assert result.verdict == Verdict.APROBADO_CON_OBSERVACIONES
    assert len(result.evidence) == 1
    assert "test coverage" in result.evidence[0].note


def test_verify_processes_all_claims_not_just_the_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La primera afirmacion no contradice nada (sigue de largo); la
    segunda si. El loop tiene que seguir mas alla de la primera."""
    (tmp_path / "README.md").write_text(
        "# demo\n\nWritten in idiomatic Python. Complete test coverage.\n"
    )
    content = _claims_payload(
        ("idiomatic Python", "Written in idiomatic Python."),
        ("complete test coverage", "Complete test coverage."),
    )
    monkeypatch.setattr(semantic_check, "get_client", lambda: _FakeClient(content=content))

    other_results = {
        "readme_check": VerifierResult(
            verdict=Verdict.NO_SOSTENIBLE,
            evidence=[
                Evidence(
                    file="README.md", line=3, note="no hay funciones de test coverage"
                )
            ],
        )
    }

    result = semantic_check.verify(RepoContext.from_path(tmp_path), other_results)

    assert result.verdict == Verdict.APROBADO_CON_OBSERVACIONES
    assert len(result.evidence) == 1
    assert "test coverage" in result.evidence[0].note


def test_locate_quote_found_returns_the_real_line_number() -> None:
    # El texto empieza con "\n" a proposito: distingue count("\n", 0, index)
    # de count("\n", 1, index) - con un primer newline mas adelante, ambos
    # arrancan en el mismo punto util y la diferencia queda invisible.
    text = "\nb\nc\nTARGET aqui\nd\n"

    assert semantic_check._locate_quote(text, "TARGET aqui") == 4


def test_locate_quote_on_first_line_returns_one() -> None:
    text = "TARGET al principio\nresto\n"

    assert semantic_check._locate_quote(text, "TARGET al principio") == 1


# Mutation testing de `_locate_quote` y la rama de cita no localizada
# (cosmic-ray sobre auditor/verifiers/semantic_check.py):
# 140 mutantes -> 92 muertos, 47 sobrevivientes, 1 incompetente.
#
# Los 47 sobrevivientes, verificados uno por uno y no asumidos:
#   44  anotaciones `X | None` (L47, L68, L127, L151). PEP 649: en 3.14 las
#       anotaciones no se evaluan en runtime, asi que mutar el `|` es invisible.
#    1  `choices[0]` -> `choices[-1]`: pedimos n=1, lista de un elemento,
#       los dos indices son el mismo. Equivalente mientras no pidamos n>1.
#    1  `index == -1` -> `index <= -1`: `str.find` devuelve -1 o >= 0, nunca
#       menos que -1. Equivalente por el contrato de find.
#    1  `index == -1` -> `index is -1`: CPython cachea los enteros -5..256,
#       asi que `is` vale exactamente cuando vale `==` para este valor.
# El incompetente es un bug del operador ExceptionReplacer de cosmic-ray
# ("'PythonNode' object has no attribute 'value'"), no del codigo.
#
# Advertencia para futuras corridas en Windows: cosmic-ray hace
# `stdout.decode("utf-8")` en la rama de KILLED (cosmic_ray/testing.py:77).
# Las notas de este proyecto llevan em-dash, pytest las emite en cp1252 al
# fallar, el decode explota y el mutante MUERTO se registra INCOMPETENT.
# La primera corrida dio 16 muertos / 77 incompetentes por eso. Con
# PYTHONIOENCODING=utf-8 da 92/1, y los 47 sobrevivientes son los mismos
# -- el bug solo subcuenta muertes, nunca convierte un sobreviviente.


def test_locate_quote_not_found_returns_none_instead_of_inventing_line_one() -> None:
    # Este test reemplaza a uno que asertaba `== 1`, o sea que fijaba el bug
    # como comportamiento esperado. Devolver 1 ante una cita que no esta en el
    # README es fabricar una ubicacion.
    text = "a\nb\nc\nd\n"

    assert semantic_check._locate_quote(text, "esto no esta en el texto") is None


def test_verify_cita_alucinada_no_inventa_una_ubicacion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El modelo devuelve una `cita_textual_del_readme` que no esta en el README.

    Antes el reporte decia `README.md:1 — README dice "<texto inventado>"`:
    una ubicacion fabricada y una atribucion falsa, en la herramienta cuyo
    proposito es no dejar pasar afirmaciones sin respaldo.
    """
    (tmp_path / "README.md").write_text(
        "# demo\n\nThis project has complete test coverage.\n"
    )
    content = _claims_payload(
        ("complete test coverage", "Esta cita jamas aparece literal en el README.")
    )
    monkeypatch.setattr(semantic_check, "get_client", lambda: _FakeClient(content=content))

    other_results = {
        "readme_check": VerifierResult(
            verdict=Verdict.NO_SOSTENIBLE,
            evidence=[
                Evidence(
                    file="README.md",
                    line=3,
                    note='README afirma "coverage" pero no hay funciones de test en el repo',
                )
            ],
        )
    }

    result = semantic_check.verify(RepoContext.from_path(tmp_path), other_results)

    assert len(result.evidence) == 1
    hallazgo = result.evidence[0]

    # 0 = "sin linea", la misma convencion que usa el resto del proyecto.
    # Cualquier numero de linea aca seria inventado.
    assert hallazgo.line == 0
    assert "cita no localizada" in hallazgo.note
    # No se le puede atribuir al README algo que el README no dice.
    assert "README dice" not in hallazgo.note
    # Y la evidencia real no se pierde: sigue citada con su archivo:linea.
    assert "no hay funciones de test" in hallazgo.note
    assert "README.md:3" in hallazgo.note


def test_verify_no_claims_is_aprobado(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "README.md").write_text("# demo\n\nJust a plain project.\n")
    monkeypatch.setattr(
        semantic_check, "get_client", lambda: _FakeClient(content=_claims_payload())
    )

    result = semantic_check.verify(RepoContext.from_path(tmp_path), {})

    assert result.verdict == Verdict.APROBADO
    assert result.evidence == []


def test_verify_malformed_json_is_observaciones_not_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "README.md").write_text("# demo\n\nProduction-ready.\n")
    monkeypatch.setattr(
        semantic_check, "get_client", lambda: _FakeClient(content="not json at all {{{")
    )

    result = semantic_check.verify(RepoContext.from_path(tmp_path), {})

    assert result.verdict == Verdict.APROBADO_CON_OBSERVACIONES
    assert len(result.evidence) == 1


def test_verify_unexpected_schema_is_observaciones_not_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "README.md").write_text("# demo\n\nProduction-ready.\n")
    monkeypatch.setattr(
        semantic_check,
        "get_client",
        lambda: _FakeClient(content=json.dumps({"algo_distinto": 42})),
    )

    result = semantic_check.verify(RepoContext.from_path(tmp_path), {})

    assert result.verdict == Verdict.APROBADO_CON_OBSERVACIONES
    assert len(result.evidence) == 1


def test_verify_timeout_is_observaciones_not_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "README.md").write_text("# demo\n\nProduction-ready.\n")
    timeout_error = groq.APITimeoutError(request=None)
    monkeypatch.setattr(semantic_check, "get_client", lambda: _FakeClient(exc=timeout_error))

    result = semantic_check.verify(RepoContext.from_path(tmp_path), {})

    assert result.verdict == Verdict.APROBADO_CON_OBSERVACIONES
    assert len(result.evidence) == 1


@pytest.mark.skipif(
    not (os.environ.get("GROQ_API_KEY") or Path(".env").is_file()),
    reason="requiere GROQ_API_KEY real",
)
def test_verify_real_api_extracts_and_cross_references(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(
        "# demo\n\nThis project has 100% test coverage and is production-ready.\n"
    )

    other_results = {
        "readme_check": VerifierResult(
            verdict=Verdict.NO_SOSTENIBLE,
            evidence=[
                Evidence(
                    file="README.md",
                    line=3,
                    note='README afirma "100% coverage" pero no hay funciones de test en el repo',
                )
            ],
        )
    }

    result = semantic_check.verify(RepoContext.from_path(tmp_path), other_results)

    assert result.verdict in (Verdict.APROBADO, Verdict.APROBADO_CON_OBSERVACIONES)
    if result.verdict == Verdict.APROBADO_CON_OBSERVACIONES:
        assert result.evidence
        assert result.evidence[0].file == "README.md"

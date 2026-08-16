import json
import os
from pathlib import Path

import groq
import pytest

from auditor.core import embedding_index
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
    assert kwargs["model"] == "llama-3.3-70b-versatile"
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


def test_locate_quote_not_found_falls_back_to_one() -> None:
    text = "a\nb\nc\nd\n"

    assert semantic_check._locate_quote(text, "esto no esta en el texto") == 1


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



# --- Paso 1 del ADR 0003: cruce semantico como refuerzo del de keywords ---


def _con_cliente(monkeypatch: pytest.MonkeyPatch, *claims: tuple[str, str]) -> None:
    monkeypatch.setattr(
        semantic_check,
        "get_client",
        lambda *a, **k: _FakeClient(content=_claims_payload(*claims)),
    )


def test_el_cruce_semantico_encuentra_lo_que_keywords_pierde(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El caso medido: "vulnerabilities" y "vulnerabilidades" no comparten
    ningun token, asi que la interseccion de keywords da vacio. El cruce
    semantico si los relaciona."""
    (tmp_path / "README.md").write_text("# demo\n\nNo known security vulnerabilities.\n")
    otros = {
        "deps_check": VerifierResult(
            verdict=Verdict.NO_SOSTENIBLE,
            evidence=[
                Evidence(
                    file="requirements.txt",
                    line=1,
                    note="pyyaml 5.3 tiene vulnerabilidades conocidas: CVE-2020-14343",
                )
            ],
        )
    }
    _con_cliente(
        monkeypatch,
        ("No known security vulnerabilities", "No known security vulnerabilities."),
    )
    monkeypatch.setattr(embedding_index, "cruzar", lambda *a, **k: [0])

    result = semantic_check.verify(RepoContext(path=tmp_path), otros)

    assert result.verdict == Verdict.APROBADO_CON_OBSERVACIONES
    assert any("CVE-2020-14343" in e.note for e in result.evidence)


def test_lo_que_encuentra_keywords_no_se_pierde_si_el_semantico_no_lo_ve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Union, no reemplazo. La calibracion mostro que cada mecanismo
    encuentra un par que el otro pierde, asi que sacar keywords seria
    cambiar un hallazgo por otro en vez de sumar."""
    (tmp_path / "README.md").write_text("# demo\n\n100% test coverage.\n")
    otros = {
        "readme_check": VerifierResult(
            verdict=Verdict.NO_SOSTENIBLE,
            evidence=[
                Evidence(file="README.md", line=1, note="no hay funciones de test reales")
            ],
        )
    }
    _con_cliente(monkeypatch, ("100% test coverage", "100% test coverage."))
    monkeypatch.setattr(embedding_index, "cruzar", lambda *a, **k: [None])

    result = semantic_check.verify(RepoContext(path=tmp_path), otros)

    assert result.verdict == Verdict.APROBADO_CON_OBSERVACIONES
    assert any("no hay funciones de test" in e.note for e in result.evidence)


def test_si_el_modelo_no_esta_disponible_se_cae_a_keywords_con_nota(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Degradacion con gracia: el cruce de keywords sigue funcionando y queda
    constancia de que el semantico no corrio - silenciar un "no corri" es
    justo lo que este proyecto existe para no dejar pasar."""
    (tmp_path / "README.md").write_text("# demo\n\n100% test coverage.\n")
    otros = {
        "readme_check": VerifierResult(
            verdict=Verdict.NO_SOSTENIBLE,
            evidence=[
                Evidence(file="README.md", line=1, note="no hay funciones de test reales")
            ],
        )
    }
    _con_cliente(monkeypatch, ("100% test coverage", "100% test coverage."))

    def _sin_modelo(*a, **k):
        raise embedding_index.ModeloNoDisponibleError("sin modelo")

    monkeypatch.setattr(embedding_index, "cruzar", _sin_modelo)

    result = semantic_check.verify(RepoContext(path=tmp_path), otros)

    assert any("no hay funciones de test" in e.note for e in result.evidence), (
        "el cruce de keywords tiene que seguir funcionando sin el modelo"
    )
    assert any("cruce semántico" in e.note for e in result.evidence), (
        "tiene que quedar constancia de que el cruce semántico no corrió"
    )


def test_sin_afirmaciones_no_se_toca_el_modelo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Carga perezosa de punta a punta: el costo fijo de ~7.6s no se paga si
    no hay nada que cruzar."""
    (tmp_path / "README.md").write_text("# demo\n")
    _con_cliente(monkeypatch)

    def _explota(*a, **k):
        raise AssertionError("no deberia tocar el modelo sin afirmaciones")

    monkeypatch.setattr(embedding_index, "cruzar", _explota)

    result = semantic_check.verify(RepoContext(path=tmp_path), {})

    assert result.verdict == Verdict.APROBADO


def test_sin_evidencia_contra_que_cruzar_no_se_toca_el_modelo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un repo donde los 4 verificadores dieron APROBADO no tiene notas contra
    las que cruzar: pagar el modelo ahi seria tirar 7.6s."""
    (tmp_path / "README.md").write_text("# demo\n\n100% test coverage.\n")
    _con_cliente(monkeypatch, ("100% test coverage", "100% test coverage."))

    def _explota(*a, **k):
        raise AssertionError("no deberia tocar el modelo sin evidencia")

    monkeypatch.setattr(embedding_index, "cruzar", _explota)

    result = semantic_check.verify(RepoContext(path=tmp_path), {})

    assert result.verdict == Verdict.APROBADO


def test_el_cruce_semantico_nunca_llega_a_no_sostenible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El techo del ADR 0002 se mantiene: el cruce semantico es una heuristica
    probabilistica, no puede tumbar un repo sano al nivel mas severo."""
    (tmp_path / "README.md").write_text("# demo\n\nNo known security vulnerabilities.\n")
    otros = {
        "deps_check": VerifierResult(
            verdict=Verdict.NO_SOSTENIBLE,
            evidence=[
                Evidence(file="req.txt", line=1, note="pyyaml tiene vulnerabilidades")
            ],
        )
    }
    _con_cliente(
        monkeypatch,
        ("No known security vulnerabilities", "No known security vulnerabilities."),
    )
    monkeypatch.setattr(embedding_index, "cruzar", lambda *a, **k: [0])

    result = semantic_check.verify(RepoContext(path=tmp_path), otros)

    assert result.verdict == Verdict.APROBADO_CON_OBSERVACIONES


@pytest.mark.slow
def test_modelo_real_el_nombre_del_proyecto_no_arrastra_el_cruce_semantico(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regresion del caso real de psf/black, contra el modelo de verdad.

    El bug del nombre de proyecto que el ADR 0002 arreglo para el cruce de
    keywords reaparece intacto con embeddings, y ahi es peor: medido, el
    falso positivo puntuaba MAS ALTO que el verdadero (0.341 vs 0.252).
    Sacando el nombre antes de embeber se invierte (0.041 vs 0.342).

    Este test corre el pipeline completo con el modelo real: la afirmacion
    sobre la licencia NO debe cruzarse con la evidencia de '_black_version'.
    """
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "black"\n')
    (tmp_path / "README.md").write_text("# demo\n\n_Black_ is licensed under MIT.\n")
    monkeypatch.setattr(
        semantic_check,
        "get_client",
        lambda *a, **k: _FakeClient(
            content=_claims_payload(
                ("Black is licensed under MIT", "_Black_ is licensed under MIT.")
            )
        ),
    )
    otros = {
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
        )
    }

    result = semantic_check.verify(RepoContext.from_path(tmp_path), otros)

    assert result.verdict == Verdict.APROBADO, (
        f"la licencia MIT no tiene nada que ver con un modulo de version "
        f"sin declarar: {result.evidence}"
    )

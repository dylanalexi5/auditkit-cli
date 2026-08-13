from pathlib import Path

import pytest

from auditor import cli
from auditor.cli import InvalidUrlError, _validate_github_url
from auditor.core.models import Verdict, VerifierResult


def test_validate_github_url_accepts_valid_url() -> None:
    url = "https://github.com/octocat/Hello-World"
    assert _validate_github_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "ext::sh -c touch pwned",
        "https://evil.com/octocat/Hello-World",
        "https://user:token@github.com/octocat/Hello-World",
        "file:///etc/passwd",
        "--upload-pack=touch pwned",
        "git@github.com:octocat/Hello-World.git",
    ],
)
def test_validate_github_url_rejects_dangerous_input(url: str) -> None:
    with pytest.raises(InvalidUrlError):
        _validate_github_url(url)


def _fake_clone(_url: str, dest: Path) -> None:
    dest.mkdir(parents=True)
    (dest / "README.md").write_text("# demo\n")


def test_main_semantic_flag_merges_result_into_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(cli, "_clone", _fake_clone)
    monkeypatch.setattr(
        cli.semantic_check,
        "verify",
        lambda ctx, other_results: VerifierResult(Verdict.APROBADO_CON_OBSERVACIONES, []),
    )

    exit_code = cli.main(["https://github.com/octocat/Hello-World", "--semantic"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "semantic_check" in output
    assert "APROBADO_CON_OBSERVACIONES" in output


def test_main_without_semantic_flag_never_calls_semantic_check(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    def _boom(*args, **kwargs):
        raise AssertionError("no deberia llamar a semantic_check sin --semantic")

    monkeypatch.setattr(cli, "_clone", _fake_clone)
    monkeypatch.setattr(cli.semantic_check, "verify", _boom)

    exit_code = cli.main(["https://github.com/octocat/Hello-World"])

    assert exit_code == 0
    assert "semantic_check" not in capsys.readouterr().out

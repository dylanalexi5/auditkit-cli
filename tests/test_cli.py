import pytest

from auditor.cli import InvalidUrlError, _validate_github_url


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

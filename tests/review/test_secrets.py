"""The secret scan: what it catches, what it leaves alone, and that it never repeats a secret."""

from __future__ import annotations

import pytest

from devloop.review.secrets import Hit, find_secrets, kinds_in

# Built at run time, so this file holds no string in a key's own format.
AWS = "AKIA" + "ABCDEFGHIJKLMNOP"
GITHUB = "ghp_" + "a" * 36
PRIVATE = "-----BEGIN " + "RSA PRIVATE KEY-----"


@pytest.mark.parametrize(
    ("line", "kind"),
    [
        (f"aws_key = '{AWS}'", "AWS access key id"),
        (f"token: {GITHUB}", "GitHub token"),
        ("github_pat_" + "A1_" * 20 + "tail", "GitHub token"),
        (PRIVATE, "private key"),
        ("-----BEGIN " + "PRIVATE KEY-----", "private key"),
        ("AIza" + "B" * 35, "Google API key"),
        ("xoxb-" + "1234567890-abcdef", "Slack token"),
        ("sk-ant-" + "api03-" + "x" * 30, "Anthropic API key"),
        ("sk-proj-" + "y" * 30, "OpenAI API key"),
        ("sk_live_" + "z" * 24, "Stripe live key"),
    ],
)
def test_a_published_key_format_is_recognised(line: str, kind: str) -> None:
    """Each provider's own format, and a private-key header, is a hit of its kind."""
    assert kinds_in(line) == [kind]


@pytest.mark.parametrize(
    "line",
    [
        "api_key = os.environ['GEMINI_API_KEY']",
        "password = 'hunter2'",
        "sha = 'ea2092837f7e22fb4d4b60ca5155e9f7d5f530d3'",
        "AKIA" + "short",
        "ghp_" + "tooshort",
        "-----BEGIN CERTIFICATE-----",
        "-----BEGIN PUBLIC KEY-----",
        "sk_test_" + "z" * 24,
        "task-ant-" + "x" * 30,
    ],
)
def test_ordinary_code_is_left_alone(line: str) -> None:
    """Names, hashes, test-mode keys and public material are not hits."""
    assert kinds_in(line) == []


def test_a_hit_names_the_file_the_line_and_the_kind_and_never_the_text() -> None:
    """A secret in a file's contents is reported with its own line number."""
    contents = {"app/settings.py": f"--- FILE: app/settings.py ---\nA = 1\nKEY = '{AWS}'\n"}
    hits = find_secrets({}, contents, header_lines=1)
    assert hits == [Hit("app/settings.py", 2, "AWS access key id")]
    assert str(hits[0]) == "app/settings.py:2 (AWS access key id)"
    assert AWS not in str(hits[0]) and AWS not in repr(hits[0])


def test_a_secret_only_in_the_diff_is_reported_without_a_line() -> None:
    """A removed line, or a file too large to attach, is still scanned through its diff."""
    hits = find_secrets({"old.py": f"@@ -1 +0,0 @@\n-TOKEN = '{GITHUB}'\n"}, {})
    assert [str(hit) for hit in hits] == ["old.py, in the diff (GitHub token)"]


def test_one_secret_in_a_file_and_its_diff_is_reported_once() -> None:
    """The contents give the line, so the diff does not repeat the hit."""
    diff = {"a.py": f"@@ -0,0 +1 @@\n+K = '{AWS}'\n"}
    contents = {"a.py": f"K = '{AWS}'\n"}
    assert find_secrets(diff, contents) == [Hit("a.py", 1, "AWS access key id")]


def test_an_allowed_path_is_not_scanned() -> None:
    """A file of test values that only look like keys is exempted by glob."""
    diff = {"tests/fixtures/keys.py": f"+K = '{AWS}'", "src/app.py": f"+K = '{AWS}'"}
    hits = find_secrets(diff, {}, allow=("tests/fixtures/*",))
    assert [hit.path for hit in hits] == ["src/app.py"]

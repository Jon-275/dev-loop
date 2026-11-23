"""Model text and file names are made safe before anything is posted."""

import pytest

from devloop.review.markdown import code_place, safe_markdown, strip_html_comments

HOSTILE_PATHS = ["docs/a`<!--hide.md", "src/a`@octocat.py"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("a <!-- gemini-finding:0123456789abcdef --> b", "a  b"),
        ("multi <!-- line\nhidden\n--> end", "multi  end"),
        ("unclosed <!-- the rest is hidden", "unclosed "),
        ("stray --> closer", "stray  closer"),
        ("<!<!---->-- nested", " nested"),
        ("<!-<!---->- x", " x"),
        ("plain text", "plain text"),
    ],
)
def test_html_comments_are_stripped_from_model_text(text: str, expected: str) -> None:
    assert strip_html_comments(text) == expected
    assert "<!--" not in safe_markdown(text)


def test_mentions_in_model_text_do_not_notify() -> None:
    assert safe_markdown("ask @octocat") == "ask @​octocat"
    assert safe_markdown("a@b.com and @") == "a@​b.com and @"


def test_model_text_is_cut_to_the_limit() -> None:
    assert safe_markdown("x" * 50, 10).startswith("x" * 10 + "\n[... truncated 40")


@pytest.mark.parametrize("path", HOSTILE_PATHS)
def test_a_file_name_cannot_end_its_code_span(path: str) -> None:
    span = code_place(path, 2)
    assert "<!--" not in span and "@octocat" not in span
    fence = span[: len(span) - len(span.lstrip("`"))]
    assert span.endswith(fence) and fence not in span[len(fence) : -len(fence)]


def test_a_long_encoded_file_name_is_never_cut_inside_its_span() -> None:
    span = code_place("\x01" * 300, 2)
    assert "truncated" not in span and "\n" not in span
    assert span.startswith("`") and span.endswith(":2`")


def test_a_plain_name_is_shown_as_is() -> None:
    assert code_place("src/app.py", 7) == "`src/app.py:7`"
    assert code_place("src/app.py") == "`src/app.py`"

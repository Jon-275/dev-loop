"""Make model text and pull-request file names safe to post on GitHub."""

from __future__ import annotations

import re

from devloop.review.model import MAX_COMMENT_CHARS
from devloop.review.prompt import prompt_path, truncate

_MENTION = re.compile(r"@(?=[A-Za-z0-9])")
_HTML_COMMENT = re.compile(r"<!--.*?(?:-->|$)", re.DOTALL)


def strip_html_comments(text: str) -> str:
    """``text`` without HTML comments, closed or not, or any stray ``<!--`` / ``-->``.

    Model text can then neither plant a finding marker (which would hide a later
    finding) nor hide text from the rendered comment. Removal repeats until
    nothing changes, so pieces left around a removed comment cannot join into
    a new ``<!--``.
    """
    while True:
        stripped = _HTML_COMMENT.sub("", text).replace("<!--", "").replace("-->", "")
        if stripped == text:
            return stripped
        text = stripped


_BACKTICKS = re.compile(r"`+")


def code_place(path: str, line: int | None = None) -> str:
    """Return a file name (and line) from the pull request as a code span safe to post.

    The name is encoded as for a prompt (:func:`prompt_path`: cut, and
    JSON-encoded unless plain), then made safe (:func:`safe_markdown`: no HTML
    comment, no ``@mention``). The span is fenced with one more backtick than
    the longest run in it, so a backtick in the name cannot end the span and
    let the rest of the name act as markdown. Every path in a posted comment
    goes through here.
    """
    # prompt_path cuts the name first, so its encoding is bounded and never cut here.
    text = safe_markdown(prompt_path(path), 6 * len(path) + 10)
    if line is not None:
        text += f":{line}"
    fence = "`" * (max((len(run) for run in _BACKTICKS.findall(text)), default=0) + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def safe_markdown(text: str, limit: int = MAX_COMMENT_CHARS) -> str:
    """Return model text safe to post: no HTML comments, no ``@mention``, within ``limit``."""
    return truncate(_MENTION.sub("@​", strip_html_comments(text)), limit)

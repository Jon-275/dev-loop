"""Look for likely secrets in what a review is about to send, before it is sent.

A review sends a pull request's diff and the contents of its changed files to a model
provider. A key committed by mistake would go with them. Every pattern here is a
provider's own published key format or a private-key header, so a match is rarely a
false alarm; nothing is guessed from how random a string looks. A file whose test
values only look like keys is exempted by path (``review.secret_allow``).

A hit names the file, the line where it is known, and the kind of key. It never carries
the matched text, so reporting a hit cannot leak what it found.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase

#: (kind, pattern): each a published key format or a private-key header.
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private key", re.compile(r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----")),
    ("AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("GitHub token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{60,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("OpenAI API key", re.compile(r"\bsk-proj-[A-Za-z0-9_-]{20,}")),
    ("Stripe live key", re.compile(r"\b[sr]k_live_[0-9A-Za-z]{20,}\b")),
)


@dataclass(frozen=True)
class Hit:
    """One likely secret: where it is and what kind, never the text itself.

    ``line`` is the line in the file's contents, or None when the match is in the
    file's diff, whose lines are not the file's.
    """

    path: str
    line: int | None
    kind: str

    def __str__(self) -> str:
        """Return ``path:line (kind)``, or ``path, in the diff (kind)``."""
        where = f"{self.path}:{self.line}" if self.line is not None else f"{self.path}, in the diff"
        return f"{where} ({self.kind})"


def kinds_in(line: str) -> list[str]:
    """Return the kind of each likely secret on one line of text, once per kind."""
    found: list[str] = []
    for kind, pattern in PATTERNS:
        if kind not in found and pattern.search(line):
            found.append(kind)
    return found


def find_secrets(
    diffs: Mapping[str, str],
    contents: Mapping[str, str],
    allow: Sequence[str] = (),
    *,
    header_lines: int = 0,
) -> list[Hit]:
    """Return every likely secret in what is about to be sent, by path.

    ``diffs`` is each file's diff section and ``contents`` each attached file's text;
    ``header_lines`` is how many lines the caller put before a file's own first line.
    A path matching an ``allow`` glob is not scanned. A secret in a file's contents is
    reported once, with its line, and not again for the same file's diff.
    """
    hits: list[Hit] = []
    for path in sorted({*diffs, *contents}):
        if any(fnmatchcase(path, glob) for glob in allow):
            continue
        seen: set[str] = set()
        for number, line in enumerate(contents.get(path, "").splitlines(), start=1):
            for kind in kinds_in(line):
                seen.add(kind)
                hits.append(Hit(path, max(number - header_lines, 1), kind))
        for line in diffs.get(path, "").splitlines():
            for kind in kinds_in(line):
                if kind not in seen:
                    seen.add(kind)
                    hits.append(Hit(path, None, kind))
    return hits

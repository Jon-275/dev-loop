"""Review threads: markers, anchors, de-duplication and what earlier reviews taught.

Threads are tagged with a hidden ``<!-- {prefix}:{key} line={n} -->`` marker. The key is
the finding's severity and path; a later finding with the same key within
:data:`LINE_WINDOW` lines of a marked thread is the same finding, so a reworded title does
not open a second thread. A finding on the same path and within
:data:`CROSS_REVIEW_WINDOW` lines of another reviewer's open thread is not posted again;
the summary says which reviewer already raised it.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from devloop.review.markdown import code_place, safe_markdown, strip_html_comments
from devloop.review.model import (
    BOT_LOGIN,
    CROSS_REVIEW_WINDOW,
    LINE_WINDOW,
    MAX_COMMENT_CHARS,
    OTHER_REVIEW_APPS,
    OWN_THREAD_DAYS,
    TITLE_SIMILARITY,
    TRUSTED_ASSOCIATIONS,
    DiffLines,
    Finding,
    Provider,
    ThreadMark,
)
from devloop.review.reply import normalise_title, title_similarity


def fingerprint(finding: Finding) -> str:
    """Return the finding's thread key: its severity and path (the line is matched in a window).

    The title is left out, so a reworded title is still the same finding; the
    line is compared within :data:`LINE_WINDOW`, so a finding that moves a
    little with an unrelated edit is too.
    """
    return hashlib.sha256(f"{finding.severity}|{finding.path}".encode()).hexdigest()[:16]


def legacy_fingerprint(finding: Finding) -> str:
    """Return the older title-based key (severity, path, title), still read from earlier threads."""
    title = " ".join(finding.title.lower().split())
    key = f"{finding.severity}|{finding.path}|{title}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def fingerprint_marker(provider: Provider, finding: Finding) -> str:
    """Return the hidden marker that tags a thread with its finding's key and reported line."""
    return f"<!-- {provider.marker_prefix}:{fingerprint(finding)} line={finding.line} -->"


def _marker_pattern(prefix: str) -> re.Pattern[str]:
    """Return the marker regex for ``prefix``: the key and the optional reported line."""
    return re.compile(rf"<!-- {re.escape(prefix)}:([0-9a-f]{{16}})(?: line=(\d+))? -->")


_ANY_BOT_MARKER = re.compile(r"<!-- ([a-z]+-finding):[0-9a-f]{16}(?: line=\d+)? -->")


def finding_heading(provider: Provider, finding: Finding) -> str:
    """Return the thread's first line: severity, title and, for Reflection, the dimension."""
    title = strip_html_comments(finding.title)
    heading = f"**{finding.severity} — {title}**"
    if finding.dimension is not None and provider.dimensions is not None:
        heading += f" · _{finding.dimension} {provider.dimensions[finding.dimension]}_"
    return heading


def anchor(
    provider: Provider, finding: Finding, lines: Mapping[str, DiffLines]
) -> dict[str, Any] | None:
    """Where to attach ``finding`` as an inline comment, or ``None`` when the diff has no line.

    The finding's own line when the diff shows it; else the nearest line the
    diff shows in that file; else the first line of the first file in the
    diff. The comment says where the finding really is whenever it moved.
    The marker is added after the model text is made safe, so it is the only one.
    """
    heading = safe_markdown(finding_heading(provider, finding), 2_000)
    marker = fingerprint_marker(provider, finding)
    where = code_place(finding.path, finding.line)
    limit = MAX_COMMENT_CHARS - len(heading) - len(marker) - 3

    def body(text: str) -> str:
        """Return the safe heading, the marker, then the safe comment text."""
        return f"{heading}\n{marker}\n\n{safe_markdown(text, limit)}"

    target = lines.get(finding.path)
    if target is not None and target.right:
        line = min(target.right, key=lambda n: (abs(n - finding.line), n))
        moved = "" if line == finding.line else f"\n\n_Reported at {where}, outside this diff._"
        return {
            "path": finding.path,
            "line": line,
            "side": "RIGHT",
            "body": body(f"{finding.body}{moved}"),
        }
    if target is not None and target.left:
        line = min(target.left, key=lambda n: (abs(n - finding.line), n))
        moved = "" if line == finding.line else f"\n\n_Reported at {where}._"
        return {
            "path": finding.path,
            "line": line,
            "side": "LEFT",
            "body": body(f"{finding.body}{moved}"),
        }
    for path in sorted(lines):
        other = lines[path]
        side, numbers = ("RIGHT", other.right) if other.right else ("LEFT", other.left)
        return {
            "path": path,
            "line": min(numbers),
            "side": side,
            "body": body(f"_In {where}, which this diff does not show._\n\n{finding.body}"),
        }
    return None


def is_own_duplicate(
    provider: Provider,
    finding: Finding,
    known: set[ThreadMark],
    is_open: Callable[[ThreadMark], bool] = lambda mark: False,
) -> bool:
    """Whether one of this reviewer's earlier threads already carries ``finding``.

    A thread holds it when it has the same key (severity and file) within
    :data:`LINE_WINDOW` lines and the same title, or, while the thread is still
    open (``is_open``), a reworded title (:data:`TITLE_SIMILARITY`). A thread
    from before titles were recorded matches on key and line. Fixed or stale
    threads are never in ``known`` (:func:`known_threads`).
    """
    key, legacy = fingerprint(finding), legacy_fingerprint(finding)
    title = normalise_title(finding.title)
    for mark in known:
        if mark.reviewer != provider.name:
            continue
        if mark.legacy and mark.legacy == legacy:
            return True
        if mark.key != key or mark.line is None or abs(mark.line - finding.line) > LINE_WINDOW:
            continue
        if not mark.title or mark.title == title:
            return True
        if title_similarity(mark.title, title) >= TITLE_SIMILARITY and is_open(mark):
            return True
    return False


def raised_by_other(
    provider: Provider, finding: Finding, known: set[ThreadMark]
) -> ThreadMark | None:
    """Another reviewer's open thread on the same path and nearby lines, if there is one."""
    nearby = [
        mark
        for mark in known
        if mark.reviewer != provider.name
        and mark.path == finding.path
        and mark.line is not None
        and abs(mark.line - finding.line) <= CROSS_REVIEW_WINDOW
    ]
    if not nearby:
        return None
    return min(nearby, key=lambda m: (abs((m.line or 0) - finding.line), m.reviewer, m.url))


#: How many accepted and rejected examples of this reviewer the prompt shows.
FEEDBACK_EXAMPLES = 3
#: How many of the top lesson classes every reviewer checks first.
PATTERNS_IN_PROMPT = 8


@dataclass(frozen=True)
class Suppression:
    """A finding maintainers rejected often enough that no thread is opened for it.

    It is still listed in the review summary, with a link to the last rejection.
    """

    fingerprint: str
    rejections: int
    url: str = ""


@dataclass(frozen=True)
class Feedback:
    """What earlier reviews taught one reviewer: prompt examples and suppressions."""

    notes: str = ""
    suppressed: Mapping[str, Suppression] = field(default_factory=dict)


def _example(record: Mapping[str, Any]) -> str:
    """One feedback record as a prompt line: severity, dimension, title and file."""
    title = " ".join(strip_html_comments(str(record.get("title") or "untitled")).split())[:200]
    return (
        f"- [{record.get('severity', '?')}/{record.get('dimension', 'unknown')}] {title} "
        f"({record.get('path') or 'unknown file'})"
    )


def _read_object(path: Path) -> dict[str, Any]:
    """Return the JSON object at ``path``, or an empty one when it is missing or unreadable."""
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def failure_patterns(path: Path | None = None) -> list[str]:
    """Prompt lines for the top recurring bug classes in the lessons file at ``path``.

    Every reviewer gets the same list, so a class one reviewer (or the council)
    caught becomes something the others check first.
    """
    data = _read_object(path) if path is not None else {}
    classes = [c for c in data.get("classes") or [] if isinstance(c, dict)]
    lines = [
        f"- {strip_html_comments(str(c.get('title') or ''))}: "
        f"{strip_html_comments(str(c.get('rule') or ''))}"
        for c in classes[:PATTERNS_IN_PROMPT]
        if c.get("title") and c.get("rule")
    ]
    if not lines:
        return []
    return ["Known failure patterns in this repo; check for these first:", *lines]


def load_feedback(
    provider: Provider, path: Path | None = None, lessons: Path | None = None
) -> Feedback:
    """Return this reviewer's feedback: the store at ``path`` and the lessons at ``lessons``.

    Both are read from the trusted (base) checkout, never the pull request's, which could
    add its own suppressions; ``None`` reads nothing. A missing or unreadable file adds
    nothing: feedback only refines a review. Records and suppressions of other reviewers
    are ignored; the known failure patterns are shared by all reviewers.
    """
    data = _read_object(path) if path is not None else {}
    patterns = failure_patterns(lessons)
    mine = sorted(
        (
            r
            for r in data.get("records") or []
            if isinstance(r, dict) and r.get("reviewer") == provider.name
        ),
        key=lambda r: str(r.get("labelled_at") or ""),
    )  # oldest label first, so the last few are the latest
    accepted = [r for r in mine if r.get("outcome") == "accepted"][-FEEDBACK_EXAMPLES:]
    rejected = [r for r in mine if r.get("outcome") == "rejected"][-FEEDBACK_EXAMPLES:]
    lines: list[str] = list(patterns)
    if accepted:
        lines.append("Previously accepted findings (do NOT re-report unless re-introduced):")
        lines += [_example(r) for r in accepted]
    if rejected:
        lines.append("Previously rejected findings (avoid re-reporting):")
        lines += [_example(r) for r in rejected]
    suppressed: dict[str, Suppression] = {}
    for entry in data.get("suppressed") or []:
        if not isinstance(entry, dict) or entry.get("reviewer") != provider.name:
            continue
        fingerprint, rejections = entry.get("fingerprint"), entry.get("rejections")
        if isinstance(fingerprint, str) and isinstance(rejections, int):
            suppressed[fingerprint] = Suppression(
                fingerprint, rejections, str(entry.get("url") or "")
            )
    return Feedback("".join(f"{line}\n" for line in lines), suppressed)


def suppression_for(finding: Finding, feedback: Feedback) -> Suppression | None:
    """Return the suppression that holds ``finding`` back, or ``None``. A P1 is never suppressed."""
    if finding.severity == "P1":
        return None
    return feedback.suppressed.get(legacy_fingerprint(finding))


def _comment_line(comment: Mapping[str, Any]) -> int | None:
    """Where a review comment sits now (``line``), else where it was posted."""
    for name in ("line", "original_line"):
        value = comment.get(name)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


_OWN_HEADING = re.compile(r"^\*\*P[123] — (.+?)\*\*", re.M)
_ACCEPTED_LABEL = re.compile(r"^\s*accepted\s*:", re.IGNORECASE)


def _when(text: object) -> datetime | None:
    """Return a GitHub timestamp, or ``None``."""
    if not isinstance(text, str):
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def retired_threads(comments: Sequence[Mapping[str, Any]], now: datetime) -> set[int]:
    """First-comment ids of threads that are fixed or stale, which hold no finding.

    Fixed: a repository member replied ``accepted:``, so a recurrence is a
    regression and gets a new thread. Stale: no comment for :data:`OWN_THREAD_DAYS`.
    """
    latest: dict[int, datetime] = {}
    fixed: set[int] = set()
    for comment in comments:
        root = comment.get("in_reply_to_id") or comment.get("id")
        if not isinstance(root, int):
            continue
        when = _when(comment.get("created_at"))
        if when is not None and (root not in latest or when > latest[root]):
            latest[root] = when
        if (
            comment.get("in_reply_to_id")
            and comment.get("author_association") in TRUSTED_ASSOCIATIONS
            and _ACCEPTED_LABEL.match(str(comment.get("body") or ""))
        ):
            fixed.add(root)
    stale = {root for root, when in latest.items() if now - when > timedelta(days=OWN_THREAD_DAYS)}
    return fixed | stale


def mark_for(
    provider: Provider, comment: Mapping[str, Any], reviewers: Mapping[str, str]
) -> list[ThreadMark]:
    """Return the thread marks a REST review comment carries, for this reviewer or another.

    ``reviewers`` maps each devloop reviewer's marker prefix to its name; another
    reviewer's marker counts only on a thread's first comment, and only by the bot.
    """
    login = (comment.get("user") or {}).get("login")
    text = str(comment.get("body") or "")
    path = str(comment.get("path") or "")
    url = str(comment.get("html_url") or "")
    if login == BOT_LOGIN:
        heading = _OWN_HEADING.search(text)
        title = normalise_title(heading.group(1)) if heading else ""
        cid = comment.get("id") if isinstance(comment.get("id"), int) else None
        own = [
            ThreadMark(provider.name, key=key, line=int(line), title=title, comment_id=cid)
            if line
            else ThreadMark(provider.name, legacy=key, comment_id=cid)
            for key, line in _marker_pattern(provider.marker_prefix).findall(text)
        ]
        if own:
            return own
        for prefix in _ANY_BOT_MARKER.findall(text):
            other = reviewers.get(prefix)
            if other is not None and other != provider.name and not comment.get("in_reply_to_id"):
                return [ThreadMark(other, path=path, line=_comment_line(comment), url=url)]
        return []
    app = OTHER_REVIEW_APPS.get(str(login))
    if app is not None and not comment.get("in_reply_to_id"):
        return [ThreadMark(app, path=path, line=_comment_line(comment), url=url)]
    return []

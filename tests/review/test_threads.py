"""Thread markers, anchors, de-duplication and review feedback."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from review_helpers import DIFF, PROVIDER

from devloop.review import threads as t
from devloop.review.gitread import diff_lines
from devloop.review.model import CROSS_REVIEW_WINDOW, Finding, ThreadMark
from devloop.review.reply import normalise_title

OTHER = replace(PROVIDER, name="Otherbot", marker_prefix="otherbot-finding")
REVIEWERS = {"testbot-finding": "Testbot", "otherbot-finding": "Otherbot"}
Severity = Literal["P1", "P2", "P3"]


def _own(title: str, line: int = 10, cid: int | None = 1, severity: Severity = "P2") -> ThreadMark:
    """Return one of this reviewer's earlier threads with a heading."""
    finding = Finding(severity, "app.py", line, title, "b")
    return ThreadMark(
        PROVIDER.name,
        key=t.fingerprint(finding),
        line=line,
        title=normalise_title(title),
        comment_id=cid,
    )


def _new(title: str, line: int = 14, severity: Severity = "P2") -> Finding:
    """Return a new finding on ``app.py``."""
    return Finding(severity, "app.py", line, title, "b")


def test_the_thread_key_ignores_the_title_but_not_severity_or_path() -> None:
    base = Finding("P1", "pyproject.toml", 24, "uv.lock not updated", "b")
    assert t.fingerprint(base) == t.fingerprint(
        Finding("P1", "pyproject.toml", 30, "Lockfile is stale", "other")
    )
    assert t.fingerprint(base) != t.fingerprint(replace(base, severity="P2"))
    assert t.fingerprint(base) != t.fingerprint(replace(base, path="uv.lock"))
    assert t.fingerprint_marker(PROVIDER, base) == (
        f"<!-- testbot-finding:{t.fingerprint(base)} line=24 -->"
    )


def test_five_reworded_findings_would_share_one_thread() -> None:
    titles = [
        "Added `openai` dependency without updating `uv.lock`",
        "uv.lock not updated after adding openai to the review dependency group",
        "uv.lock not updated for new openai dependency in review group",
        "Added openai dependency to review group without updating uv.lock",
        "pyproject.toml updated without updating uv.lock",
    ]
    first = Finding("P1", "pyproject.toml", 24, titles[0], "b")
    known = {ThreadMark(PROVIDER.name, key=t.fingerprint(first), line=24)}
    for title in titles[1:]:
        assert t.is_own_duplicate(PROVIDER, replace(first, title=title), known)


def test_a_different_finding_near_an_own_thread_is_not_a_duplicate() -> None:
    known = {_own("Retry loop has no cap")}
    assert not t.is_own_duplicate(
        PROVIDER, _new("SQL built by string concat"), known, lambda m: True
    )


def test_the_same_title_is_a_duplicate_without_asking_github() -> None:
    asked: list[ThreadMark] = []

    def is_open(mark: ThreadMark) -> bool:
        """Record that GitHub would have been asked."""
        asked.append(mark)
        return True

    known = {_own("Retry loop has no cap")}
    assert t.is_own_duplicate(PROVIDER, _new("  retry LOOP has no cap "), known, is_open)
    assert asked == []


def test_a_reworded_title_is_held_only_by_an_open_thread() -> None:
    known = {_own("Added openai dependency without updating uv.lock", line=24)}
    reworded = _new("uv.lock not updated after adding openai to the review group", line=24)
    assert t.is_own_duplicate(PROVIDER, reworded, known, lambda mark: True)
    assert not t.is_own_duplicate(PROVIDER, reworded, known, lambda mark: False)


def test_a_finding_is_left_to_another_reviewers_nearby_open_thread() -> None:
    other = ThreadMark("Otherbot", path="app.py", line=12, url="u")
    near = Finding("P1", "app.py", 14, "t", "b")
    assert t.raised_by_other(PROVIDER, near, {other}) == other
    far = replace(near, line=12 + CROSS_REVIEW_WINDOW + 1)
    assert t.raised_by_other(PROVIDER, far, {other}) is None
    assert t.raised_by_other(PROVIDER, replace(near, path="lib.py"), {other}) is None
    assert t.raised_by_other(OTHER, near, {other}) is None


def _rest(
    cid: int, body: str, days_ago: int, reply_to: int | None = None, **extra: object
) -> dict[str, Any]:
    """Return a REST review comment ``days_ago`` days old."""
    when = (datetime.now(UTC) - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")
    comment = {"id": cid, "body": body, "created_at": when, "path": "app.py", "line": 10}
    if reply_to is not None:
        comment["in_reply_to_id"] = reply_to
    return comment | extra


BOT = {"login": "github-actions[bot]"}


def test_fixed_or_stale_own_threads_hold_nothing() -> None:
    marker = t.fingerprint_marker(PROVIDER, _new("Retry loop has no cap", line=10))
    comments = [
        _rest(1, f"**P2 — Retry loop has no cap**\n{marker}", 2, user=BOT),
        _rest(2, "accepted: fixed in abc", 1, reply_to=1, author_association="OWNER"),
        _rest(3, f"**P2 — Retry loop has no cap**\n{marker}", 40, user=BOT),
        _rest(4, f"**P2 — Retry loop has no cap**\n{marker}", 1, user=BOT),
        _rest(5, "accepted: by a stranger", 1, reply_to=4, author_association="NONE"),
    ]
    assert t.retired_threads(comments, datetime.now(UTC)) == {1, 3}


def test_only_the_bot_and_known_apps_carry_marks() -> None:
    marker = t.fingerprint_marker(PROVIDER, _new("Leak", line=10))
    own = t.mark_for(PROVIDER, _rest(1, f"**P2 — Leak**\n{marker}", 0, user=BOT), REVIEWERS)
    assert own == [
        ThreadMark("Testbot", key=t.fingerprint(_new("Leak")), line=10, title="leak", comment_id=1)
    ]
    forged = _rest(2, f"**P2 — Leak**\n{marker}", 0, user={"login": "author"})
    assert t.mark_for(PROVIDER, forged, REVIEWERS) == []
    other = t.mark_for(OTHER, _rest(1, f"x\n{marker}", 0, user=BOT, html_url="u"), REVIEWERS)
    assert other == [ThreadMark("Testbot", path="app.py", line=10, url="u")]
    app = _rest(3, "Greptile says", 0, user={"login": "greptile-apps[bot]"})
    assert t.mark_for(PROVIDER, app, REVIEWERS)[0].reviewer == "Greptile"
    assert t.mark_for(PROVIDER, {**app, "in_reply_to_id": 1}, REVIEWERS) == []


def test_an_older_title_marker_still_counts_as_the_thread() -> None:
    finding = _new("Retry loop has no cap", line=10, severity="P1")
    legacy = f"<!-- testbot-finding:{t.legacy_fingerprint(finding)} -->"
    [mark] = t.mark_for(PROVIDER, _rest(7, f"**P1 — x**\n{legacy}", 0, user=BOT), REVIEWERS)
    assert t.is_own_duplicate(PROVIDER, finding, {mark})


def test_a_finding_on_a_diff_line_is_attached_there() -> None:
    comment = t.anchor(PROVIDER, Finding("P1", "app.py", 12, "Bug", "Fix."), diff_lines(DIFF))
    assert comment is not None
    assert (comment["path"], comment["line"], comment["side"]) == ("app.py", 12, "RIGHT")
    assert comment["body"].startswith("**P1 — Bug**") and "outside this diff" not in comment["body"]


def test_a_finding_outside_the_diff_moves_to_the_nearest_line_and_says_so() -> None:
    comment = t.anchor(PROVIDER, Finding("P1", "app.py", 40, "Bug", "Fix."), diff_lines(DIFF))
    assert comment is not None and (comment["line"], comment["side"]) == (13, "RIGHT")
    assert "Reported at `app.py:40`, outside this diff" in comment["body"]


def test_deleted_and_unshown_files_still_get_a_thread() -> None:
    lines = diff_lines(DIFF)
    deleted = t.anchor(PROVIDER, Finding("P1", "gone.py", 2, "Bug", "Fix."), lines)
    assert deleted is not None and (deleted["line"], deleted["side"]) == (2, "LEFT")
    elsewhere = t.anchor(PROVIDER, Finding("P1", "elsewhere.py", 5, "Bug", "Fix."), lines)
    assert elsewhere is not None and (elsewhere["path"], elsewhere["line"]) == ("app.py", 10)
    assert "In `elsewhere.py:5`, which this diff does not show" in elsewhere["body"]
    assert t.anchor(PROVIDER, Finding("P1", "app.py", 1, "Bug", "Fix."), {}) is None


def test_model_text_cannot_plant_or_hide_a_marker() -> None:
    planted = "<!-- testbot-finding:0123456789abcdef line=12 -->"
    finding = Finding("P2", "app.py", 12, f"Title {planted}", f"Body <!-- x --> @octocat {planted}")
    comment = t.anchor(PROVIDER, finding, diff_lines(DIFF))
    assert comment is not None and comment["body"].count("<!--") == 1
    assert "0123456789abcdef" not in comment["body"] and "@​octocat" in comment["body"]


def _store(tmp_path: Path, reviewer: str, *held: tuple[Severity, str]) -> Path:
    """Write a feedback store suppressing ``held`` (severity, title) for ``reviewer``."""
    findings = [Finding(sev, "app.py", 2, title, "b") for sev, title in held]
    records = [
        {"reviewer": reviewer, "outcome": "rejected", "title": "Noisy nit", "path": "a.py"},
        {"reviewer": "Someone", "outcome": "accepted", "title": "Not mine", "path": "a.py"},
    ]
    suppressed = [
        {"reviewer": reviewer, "fingerprint": t.legacy_fingerprint(f), "rejections": 4, "url": "u"}
        for f in findings
    ]
    path = tmp_path / "store.json"
    path.write_text(json.dumps({"records": records, "suppressed": suppressed}))
    return path


def test_feedback_is_per_reviewer_and_a_p1_is_never_suppressed(tmp_path: Path) -> None:
    store = _store(tmp_path, "Testbot", ("P2", "Noisy"), ("P1", "Severe"))
    mine = t.load_feedback(PROVIDER, store, None)
    assert t.load_feedback(OTHER, store, None).suppressed == {}
    assert len(mine.suppressed) == 2 and "Noisy nit" in mine.notes and "Not mine" not in mine.notes
    noisy = t.suppression_for(Finding("P2", "app.py", 2, "Noisy", "b"), mine)
    assert noisy is not None and noisy.rejections == 4
    assert t.suppression_for(Finding("P1", "app.py", 2, "Severe", "b"), mine) is None


def test_the_top_lesson_classes_are_listed_first_in_rank_order(tmp_path: Path) -> None:
    lessons = tmp_path / "lessons.json"
    classes = [{"title": f"Class {n}", "rule": f"Rule {n}."} for n in range(12)]
    lessons.write_text(json.dumps({"classes": classes}))
    notes = t.load_feedback(PROVIDER, None, lessons).notes
    assert notes.startswith("Known failure patterns in this repo; check for these first:")
    assert notes.index("- Class 0: Rule 0.") < notes.index("- Class 7: Rule 7.")
    assert "Class 8" not in notes


def test_no_feedback_or_an_unreadable_file_adds_nothing(tmp_path: Path) -> None:
    (tmp_path / "bad.json").write_text("{not json")
    assert t.load_feedback(PROVIDER, None, None) == t.Feedback()
    assert t.load_feedback(PROVIDER, tmp_path / "bad.json", tmp_path / "none.json") == t.Feedback()


def test_the_prompt_takes_the_latest_labelled_examples(tmp_path: Path) -> None:
    records = [
        {
            "reviewer": "Testbot",
            "outcome": "accepted",
            "title": f"Finding {n}",
            "path": "a.py",
            "labelled_at": f"2026-11-{n:02d}T00:00:00Z",
        }
        for n in (9, 8, 7, 6, 2, 1)
    ]
    store = tmp_path / "store.json"
    store.write_text(json.dumps({"records": records}))
    notes = t.load_feedback(PROVIDER, store, None).notes
    assert [n for n in (1, 2, 6, 7, 8, 9) if f"Finding {n} " in notes] == [7, 8, 9]

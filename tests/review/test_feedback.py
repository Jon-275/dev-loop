"""Collecting labelled review findings into the feedback store."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from review_helpers import PROVIDER

from devloop.cli import main
from devloop.review import feedback as fb
from devloop.review.github import Reply
from devloop.review.model import DiffLines, Finding
from devloop.review.threads import anchor, legacy_fingerprint

REVIEWERS = {"testbot-finding": "Testbot"}
PREFIXES = ("accepted", "rejected")


def _bot(title: str = "Stale field") -> dict[str, Any]:
    """Return the first comment of a thread the bot opened for one finding."""
    found = Finding("P2", "crm_sync.py", 10, title, "b")
    comment = anchor(PROVIDER, found, {"crm_sync.py": DiffLines(right={10})})
    assert comment is not None
    return {"body": comment["body"], "url": "https://g/1", "author": {"login": "github-actions"}}


def _reply(
    text: str, login: str = "maintainer", association: str = "OWNER", when: str = "t1"
) -> dict[str, Any]:
    """Return a reply in the thread, by a repository owner unless said otherwise."""
    return {
        "body": text,
        "createdAt": when,
        "authorAssociation": association,
        "author": {"login": login},
    }


def _thread(*comments: dict[str, Any], resolved: bool = True, tid: str = "T1") -> dict[str, Any]:
    """Return a review thread on ``crm_sync.py``."""
    return {"id": tid, "isResolved": resolved, "path": "crm_sync.py", "comments": list(comments)}


@pytest.mark.parametrize(
    ("reply", "outcome"),
    [
        (_reply("accepted: fixed in abc123, test added"), "accepted"),
        (_reply("Rejected: by design.", association="MEMBER"), "rejected"),
        (
            _reply("  REJECTED : the lockfile is in the diff", association="COLLABORATOR"),
            "rejected",
        ),
    ],
)
def test_a_labelled_member_reply_decides(reply: dict[str, Any], outcome: str) -> None:
    record = fb.classify_thread(_thread(_bot(), reply), REVIEWERS, PREFIXES)
    assert record is not None and record["outcome"] == outcome
    assert (record["reviewer"], record["severity"], record["title"]) == (
        "Testbot",
        "P2",
        "Stale field",
    )
    assert record["fingerprint"] == legacy_fingerprint(
        Finding("P2", "crm_sync.py", 10, "Stale field", "b")
    )


@pytest.mark.parametrize(
    "thread",
    [
        _thread(_bot(), _reply("thanks, fixed")),
        _thread(_bot(), _reply("accepted: x", login="fork-author", association="CONTRIBUTOR")),
        _thread(_bot(), _reply("accepted: x", login="ci[bot]")),
        _thread(_bot()),
        _thread(_bot(), _reply("accepted: x"), resolved=False),
        _thread({**_bot(), "author": {"login": "someone"}}, _reply("accepted: x")),
        _thread(
            {**_bot(), "body": _bot()["body"].replace("testbot", "otherbot")}, _reply("accepted: x")
        ),
    ],
    ids=[
        "unlabelled",
        "fork-author",
        "bot",
        "no-reply",
        "unresolved",
        "copied-marker",
        "other-reviewer",
    ],
)
def test_anything_but_a_labelled_member_reply_on_a_resolved_finding_is_unknown(
    thread: dict[str, Any],
) -> None:
    assert fb.classify_thread(thread, REVIEWERS, PREFIXES) is None


def test_the_latest_labelled_reply_wins_and_records_when() -> None:
    thread = _thread(
        _bot(), _reply("rejected: no", when="t1"), _reply("accepted: on reflection", when="t2")
    )
    record = fb.classify_thread(thread, REVIEWERS, PREFIXES)
    assert record is not None and (record["outcome"], record["labelled_at"]) == ("accepted", "t2")


def test_a_review_apps_finding_feeds_the_lessons_but_is_never_suppressed() -> None:
    body = '<img alt="P2" src="x"> **Retry loop has no cap.**\n\nDetails.'
    app = {"body": body, "url": "u", "author": {"login": "greptile-apps"}}
    record = fb.classify_thread(_thread(app, _reply("rejected: no")), REVIEWERS, PREFIXES)
    assert record is not None and (record["reviewer"], record["title"]) == (
        "Greptile",
        "Retry loop has no cap",
    )
    assert fb.suppressions([record, record, record], ["Testbot"]) == []


def test_the_configured_label_words_are_the_ones_read() -> None:
    thread = _thread(_bot(), _reply("fixed: in abc"))
    assert fb.classify_thread(thread, REVIEWERS, PREFIXES) is None
    record = fb.classify_thread(thread, REVIEWERS, ("fixed", "wontfix"))
    assert record is not None and record["outcome"] == "fixed"


def _record(
    outcome: str, n: int, severity: str = "P2", reviewer: str = "Testbot"
) -> dict[str, Any]:
    """Return a stored record of thread ``n``."""
    return {
        "reviewer": reviewer,
        "fingerprint": "f",
        "severity": severity,
        "outcome": outcome,
        "pr": n,
        "thread": f"T{n}",
        "url": f"u{n}",
    }


def test_a_finding_rejected_three_times_is_suppressed_with_its_last_link_never_a_p1() -> None:
    records = [_record("rejected", n) for n in (1, 2, 3)]
    assert fb.suppressions(records, ["Testbot"]) == [
        {"reviewer": "Testbot", "fingerprint": "f", "rejections": 3, "url": "u3"}
    ]
    assert fb.suppressions(records[:2], ["Testbot"]) == []
    assert fb.suppressions([_record("rejected", n, "P1") for n in (1, 2, 3)], ["Testbot"]) == []


class Api:
    """A fake GitHub API: one merged PR (#5) with one labelled thread."""

    def __init__(self, failures: int = 0) -> None:
        """Fail the first ``failures`` requests with a 502."""
        self.failures = failures
        self.sent: list[str] = []

    def __call__(self, method: str, url: str, token: str, body: object) -> Reply:
        """Answer the pulls listing or the threads query."""
        self.sent.append(url)
        if self.failures:
            self.failures -= 1
            return Reply(502, None)
        if "/pulls?" in url:
            now = "2999-01-01T00:00:00Z"
            return Reply(
                200,
                [
                    {"number": 5, "updated_at": now, "merged_at": now},
                    {"number": 4, "updated_at": now, "merged_at": None},
                ],
            )
        thread = _thread(_bot(), _reply("rejected: by design"))
        nodes = [{**thread, "comments": {"nodes": thread["comments"], "pageInfo": {}}}]
        pr = {"reviewThreads": {"nodes": nodes, "pageInfo": {"hasNextPage": False}}}
        return Reply(200, {"data": {"repository": {"pullRequest": pr}}})


def test_collect_adds_new_records_and_keeps_old_ones_by_thread() -> None:
    old = {"records": [_record("accepted", 1)]}
    store = fb.collect("t", "o/r", old, REVIEWERS, PREFIXES, send=Api(), sleep=lambda _: None)
    assert [(r["pr"], r["outcome"]) for r in store["records"]] == [(1, "accepted"), (5, "rejected")]
    again = fb.collect("t", "o/r", store, REVIEWERS, PREFIXES, send=Api(), sleep=lambda _: None)
    assert again == store


def test_a_brief_github_error_is_retried_and_a_lasting_one_writes_nothing() -> None:
    waits: list[float] = []
    fb.collect("t", "o/r", {}, REVIEWERS, PREFIXES, send=Api(failures=2), sleep=waits.append)
    assert waits == [15.0, 30.0]
    with pytest.raises(fb.FeedbackError, match="502"):
        fb.collect("t", "o/r", {}, REVIEWERS, PREFIXES, send=Api(failures=3), sleep=lambda _: None)


def test_a_store_that_cannot_be_read_is_never_overwritten(tmp_path: Path) -> None:
    assert fb.load_store(tmp_path / "none.json") == fb.EMPTY_STORE
    (tmp_path / "bad.json").write_text("{not json")
    with pytest.raises(fb.FeedbackError, match="cannot be read"):
        fb.load_store(tmp_path / "bad.json")


def test_the_command_needs_its_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "devloop.toml").write_text('[framework]\nversion = "0.3"\n')
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert main(["feedback", "--root", str(tmp_path)]) == 2
    assert "set review.feedback" in capsys.readouterr().err


def test_the_command_writes_the_store_only_after_a_full_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "devloop.toml").write_text(
        '[framework]\nversion = "0.3"\n[review]\nfeedback = "data/feedback.json"\n'
    )
    env = {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "o/r"}

    def failing(*args: object, **kwargs: object) -> dict[str, Any]:
        """Fail as a refused read would."""
        raise fb.FeedbackError("GitHub refused GET (502)")

    monkeypatch.setattr(fb, "collect", failing)
    assert fb.main(tmp_path, env) == 1
    assert not (tmp_path / "data" / "feedback.json").exists()
    monkeypatch.setattr(
        fb, "collect", lambda *a, **k: {"version": 2, "records": [], "suppressed": []}
    )
    assert fb.main(tmp_path, env) == 0
    assert json.loads((tmp_path / "data" / "feedback.json").read_text())["version"] == 2

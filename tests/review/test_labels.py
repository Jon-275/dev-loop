"""``devloop labels``: every review finding has a labelled reply before the status passes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from review_helpers import PROVIDER

from devloop.review import feedback as fb
from devloop.review import labels as lb
from devloop.review.github import Reply
from devloop.review.model import DiffLines, Finding
from devloop.review.threads import anchor

REVIEWERS = {"testbot-finding": "Testbot"}
PREFIXES = ("accepted", "rejected")
HEAD = "a" * 40


def _bot() -> dict[str, Any]:
    """Return the first comment of a thread the bot opened for one finding."""
    comment = anchor(
        PROVIDER, Finding("P2", "app.py", 3, "Leak", "b"), {"app.py": DiffLines(right={3})}
    )
    assert comment is not None
    return {"body": comment["body"], "url": "https://g/1", "author": {"login": "github-actions"}}


def _thread(*replies: str, resolved: bool = True) -> dict[str, Any]:
    """Return a finding thread with owner replies ``replies``."""
    comments = [_bot()] + [
        {"body": r, "authorAssociation": "OWNER", "author": {"login": "owner"}} for r in replies
    ]
    return {"id": "T", "isResolved": resolved, "path": "app.py", "comments": comments}


def test_every_finding_thread_needs_a_labelled_reply_resolved_or_not() -> None:
    threads = [_thread("accepted: fixed"), _thread(), _thread("thanks", resolved=False)]
    state, description, lines = lb.verdict(lb.findings(threads, REVIEWERS, PREFIXES), PREFIXES)
    assert state == "failure"
    assert description == "2 of 3 finding thread(s) need a accepted: or rejected: reply"
    assert lines == [
        "- Testbot finding on app.py (resolved): https://g/1",
        "- Testbot finding on app.py (open): https://g/1",
    ]
    ok = lb.verdict(lb.findings([_thread("rejected: no")], REVIEWERS, PREFIXES), PREFIXES)
    assert ok[0] == "success" and ok[1].startswith("All 1 finding thread(s)")
    assert lb.verdict([], PREFIXES)[:2] == ("success", "No review findings on this pull request")


STATUS = [{"context": "Testbot review", "state": "success"}]
DONE = [{"name": "Greptile Review", "status": "completed", "conclusion": "success"}]


@pytest.mark.parametrize(
    ("statuses", "checks", "reasons"),
    [
        (STATUS, DONE, []),
        ([], DONE, ["Waiting for Testbot review"]),
        ([{"context": "Testbot review", "state": "pending"}], DONE, ["Waiting for Testbot review"]),
        (
            [{"context": "Testbot review", "state": "error"}],
            DONE,
            ["Testbot review did not finish"],
        ),
        (STATUS, [], ["Waiting for Greptile Review"]),
        (STATUS, [{**DONE[0], "status": "in_progress"}], ["Waiting for Greptile Review"]),
        (STATUS, [{**DONE[0], "conclusion": "cancelled"}], ["Greptile Review did not finish"]),
    ],
    ids=["done", "no-status", "pending", "error", "no-check", "running", "cancelled"],
)
def test_a_review_that_has_not_finished_is_never_a_pass(
    statuses: list[dict[str, str]], checks: list[dict[str, str]], reasons: list[str]
) -> None:
    assert lb.waiting_for(statuses, checks, ["Testbot review"], ["Greptile Review"]) == reasons


class Api:
    """A fake GitHub: a PR whose head has ``statuses``, with ``threads``; records posts."""

    def __init__(
        self, threads: list[dict[str, Any]], statuses: list[dict[str, str]], fail: str = ""
    ) -> None:
        """Keep what the API answers; ``fail`` names a URL part that answers 403."""
        self.threads, self.statuses, self.fail = threads, statuses, fail
        self.posted: list[tuple[str, str]] = []

    def __call__(self, method: str, url: str, token: str, body: object) -> Reply:
        """Answer one request."""
        if self.fail and self.fail in url:
            return Reply(403, {})
        if method == "POST" and "/statuses/" in url:
            assert isinstance(body, dict)
            self.posted.append((url.rsplit("/", 1)[1], body["state"]))
            return Reply(201, {})
        if url.endswith("/pulls/7"):
            return Reply(200, {"head": {"sha": HEAD}})
        if "/status?" in url:
            return Reply(200, {"statuses": self.statuses})
        nodes = [{**t, "comments": {"nodes": t["comments"], "pageInfo": {}}} for t in self.threads]
        pr = {"reviewThreads": {"nodes": nodes, "pageInfo": {"hasNextPage": False}}}
        return Reply(200, {"data": {"repository": {"pullRequest": pr}}})


def _run(
    api: Api, tmp_path: Path, wait_for: tuple[str, ...] = ("Testbot review",)
) -> tuple[int, Path]:
    """Run the check against ``api``; return the exit code and the outputs file."""
    out = tmp_path / "out.txt"
    gh = lb.Github("t", "o/r", send=api, sleep=lambda _: None)
    code = lb.run(gh, 7, "run", REVIEWERS, PREFIXES, wait_for, event_sha="e" * 40, outputs=str(out))
    return code, out


def test_the_head_goes_pending_first_then_gets_the_verdict(tmp_path: Path) -> None:
    api = Api([_thread("accepted: fixed")], STATUS)
    code, out = _run(api, tmp_path)
    assert code == 0 and api.posted == [(HEAD, "pending"), (HEAD, "success")]
    assert out.read_text() == f"sha={HEAD}\nposted=true\n"


def test_an_unlabelled_thread_fails_and_a_running_review_keeps_it_pending(tmp_path: Path) -> None:
    api = Api([_thread()], STATUS)
    assert _run(api, tmp_path)[0] == 1 and api.posted[-1] == (HEAD, "failure")
    waiting = Api([_thread("accepted: fixed")], [])
    assert _run(waiting, tmp_path)[0] == 0 and waiting.posted[-1] == (HEAD, "pending")


def test_a_failed_read_posts_a_failure_never_a_pass(tmp_path: Path) -> None:
    api = Api([], STATUS, fail="graphql")
    with pytest.raises(fb.FeedbackError):
        _run(api, tmp_path)
    assert api.posted == [(HEAD, "pending"), (HEAD, "failure")]


def test_an_unreadable_pull_request_fails_the_event_head(tmp_path: Path) -> None:
    api = Api([], STATUS, fail="/pulls/7")
    with pytest.raises(fb.FeedbackError):
        _run(api, tmp_path)
    assert api.posted == [("e" * 40, "failure")]


def test_the_command_needs_its_settings(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "devloop.toml").write_text('[framework]\nversion = "0.3"\n')
    assert (
        lb.main(tmp_path, {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "o/r", "PR_NUMBER": "x"}) == 2
    )
    assert "numeric PR_NUMBER" in capsys.readouterr().err


def test_a_check_name_is_sent_url_encoded() -> None:
    sent: list[str] = []

    def api(method: str, url: str, token: str, body: object) -> Reply:
        """Record the URL and answer with no runs."""
        sent.append(url)
        return Reply(200, {"statuses": [], "check_runs": []})

    lb.Github("t", "o/r", send=api, sleep=lambda _: None).reviews(HEAD, ["Lint & Test (C++)"])
    assert sent[-1].endswith("check_name=Lint%20%26%20Test%20%28C%2B%2B%29&filter=latest")

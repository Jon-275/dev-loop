"""The review's GitHub calls, against a fake API: threads, the last review, posting."""

from __future__ import annotations

import urllib.request
from datetime import UTC, datetime
from typing import Any

import pytest
from review_helpers import PROVIDER

from devloop.review import github as gh
from devloop.review.model import (
    MAX_COMMENTS_PER_REVIEW,
    Baseline,
    Finding,
    ReviewError,
    Settings,
)
from devloop.review.threads import fingerprint_marker

SETTINGS = Settings("k", "t", "o/r", "7", "c" * 40, "d" * 40)
BOT = {"login": "github-actions[bot]"}
HEAD, BASE = "a" * 40, "b" * 40
NOW = datetime.now(UTC).isoformat().replace("+00:00", "Z")


class FakeApi:
    """Answer requests from canned replies keyed by a URL fragment, recording each one."""

    def __init__(self, **replies: list[gh.Reply]) -> None:
        """Keep the replies; each key is matched against the request URL."""
        self.replies = replies
        self.sent: list[tuple[str, str, object]] = []

    def bodies(self) -> list[dict[str, Any]]:
        """Return the JSON bodies sent, in order."""
        return [body for _, _, body in self.sent if isinstance(body, dict)]

    def __call__(self, method: str, url: str, token: str, body: object) -> gh.Reply:
        """Return the next canned reply for the first key found in ``url``."""
        assert token == "t"
        self.sent.append((method, url, body))
        for key, queue in self.replies.items():
            if key in url:
                return queue.pop(0)
        raise AssertionError(f"unexpected request {method} {url}")


def _threads(*pages: tuple[list[dict[str, Any]], bool]) -> list[gh.Reply]:
    """Return GraphQL replies, one per page of (threads, has next page)."""
    return [
        gh.Reply(
            200,
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "nodes": nodes,
                                "pageInfo": {"hasNextPage": more, "endCursor": "c"},
                            }
                        }
                    }
                }
            },
        )
        for nodes, more in pages
    ]


def _thread(cid: int, resolved: bool = False) -> dict[str, Any]:
    """Return one review thread whose first comment is ``cid``."""
    return {"isResolved": resolved, "comments": {"nodes": [{"databaseId": cid}]}}


def test_open_threads_are_read_across_pages() -> None:
    api = FakeApi(graphql=_threads(([_thread(1), _thread(2, True)], True), ([_thread(3)], False)))
    assert gh.open_thread_comment_ids(SETTINGS, api) == {1, 3}
    assert [body["variables"]["after"] for body in api.bodies()] == [None, "c"]


@pytest.mark.parametrize("reply", [gh.Reply(502, None), gh.Reply(200, {"data": None})])
def test_a_failed_thread_query_fails_the_check(reply: gh.Reply) -> None:
    with pytest.raises(ReviewError, match="review threads|review-thread"):
        gh.open_thread_comment_ids(SETTINGS, FakeApi(graphql=[reply]))


def test_known_threads_keep_own_marks_and_only_open_threads_of_others() -> None:
    marker = fingerprint_marker(PROVIDER, Finding("P2", "app.py", 10, "Leak", "b"))
    other = "<!-- otherbot-finding:0123456789abcdef line=4 -->"
    comments = [
        {"id": 1, "user": BOT, "body": f"**P2 — Leak**\n{marker}", "path": "app.py", "line": 10},
        {"id": 2, "user": BOT, "body": f"x\n{other}", "path": "a.py", "line": 4, "created_at": NOW},
        {"id": 3, "user": BOT, "body": f"y\n{other}", "path": "b.py", "line": 4, "created_at": NOW},
    ]
    api = FakeApi(
        comments=[
            gh.Reply(200, comments[:2], "https://api.github.com/next/comments"),
            gh.Reply(200, comments[2:]),
        ],
        graphql=_threads(([_thread(2), _thread(3, True)], False)),
    )
    marks = gh.known_threads(SETTINGS, PROVIDER, {"otherbot-finding": "Otherbot"}, api)
    assert {(m.reviewer, m.path or m.title) for m in marks} == {
        ("Testbot", "leak"),
        ("Otherbot", "a.py"),
    }


def test_a_refused_listing_is_never_read_as_empty() -> None:
    with pytest.raises(ReviewError, match="refused to list review comments"):
        gh.known_threads(SETTINGS, PROVIDER, {}, FakeApi(comments=[gh.Reply(403, {})]))


def test_every_finding_is_posted_in_batches() -> None:
    comments = [
        {"path": "a.py", "line": n, "body": "b"} for n in range(MAX_COMMENTS_PER_REVIEW + 1)
    ]
    api = FakeApi(reviews=[gh.Reply(200, {}), gh.Reply(200, {})])
    gh.post_review(SETTINGS, PROVIDER, "Summary", comments, api)
    first, second = api.bodies()
    assert first["body"] == "Summary" and len(first["comments"]) == MAX_COMMENTS_PER_REVIEW
    assert second["body"].endswith("part 2 of 2") and second["event"] == "COMMENT"


def test_a_refused_review_fails_the_check() -> None:
    with pytest.raises(ReviewError, match="refused the review"):
        gh.post_review(SETTINGS, PROVIDER, "S", [], FakeApi(reviews=[gh.Reply(422, {})]))


def _review(
    rid: int, unreviewed: int = 0, user: dict[str, str] = BOT, base: str = BASE
) -> dict[str, Any]:
    """Return a summary review on ``HEAD`` as the bot posts it."""
    marker = f"<!-- testbot-review findings=0 posted=0 not_posted=0 unreviewed={unreviewed}"
    marker += f" base={base} -->" if base else " -->"
    return {"id": rid, "user": user, "commit_id": HEAD, "body": f"### Review\n\n{marker}"}


SUCCESS = {"context": "Testbot review", "state": "success", "creator": BOT}


def _lookup(reviews: list[Any], statuses: list[Any], status: int = 200) -> Baseline | None:
    """Run ``previous_review`` against canned review and status listings."""
    api = FakeApi(reviews=[gh.Reply(status, reviews)], statuses=[gh.Reply(200, statuses)])
    return gh.previous_review(SETTINGS, PROVIDER, api)


def test_the_last_completed_review_is_the_bots_summary_with_a_success_status() -> None:
    reviews = [_review(1, base="e" * 40), _review(2), {"id": 3, "user": BOT, "body": "part 2"}]
    assert _lookup(reviews, [SUCCESS]) == Baseline(HEAD, BASE)


@pytest.mark.parametrize(
    ("reviews", "statuses"),
    [
        ([], [SUCCESS]),
        ([_review(1, user={"login": "author"})], [SUCCESS]),
        ([_review(1), _review(2, unreviewed=1)], [SUCCESS]),
        ([_review(1, base="")], [SUCCESS]),
        ([_review(1)], [{**SUCCESS, "state": "failure"}, SUCCESS]),
        ([_review(1)], [{**SUCCESS, "creator": {"login": "author"}}]),
        ([_review(1)], [{**SUCCESS, "context": "Other review"}]),
    ],
    ids=["none", "author", "incomplete", "old-marker", "failed", "author-status", "other"],
)
def test_no_completed_review_means_a_full_review(reviews: list[Any], statuses: list[Any]) -> None:
    assert _lookup(reviews, statuses) is None


def test_a_failed_lookup_fails_the_check() -> None:
    with pytest.raises(ReviewError, match="refused to list"):
        _lookup([], [], status=502)


class _Response:
    """A minimal ``urlopen`` response."""

    status = 200
    headers = {"Link": '<https://api.github.com/x?page=2>; rel="next", <z>; rel="last"'}

    def read(self) -> bytes:
        """Return the JSON body."""
        return b"[1, 2]"

    def __enter__(self) -> _Response:
        """Enter the context."""
        return self

    def __exit__(self, *_: object) -> None:
        """Leave the context."""


def test_the_token_is_sent_only_in_an_unredirected_header(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[urllib.request.Request] = []

    def urlopen(request: urllib.request.Request, timeout: float) -> _Response:
        """Record the request and answer it."""
        seen.append(request)
        return _Response()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    reply = gh.http("GET", "https://api.github.com/x", "secret")
    assert (reply.status, reply.data, reply.next_url) == (
        200,
        [1, 2],
        "https://api.github.com/x?page=2",
    )
    [request] = seen
    assert request.unredirected_hdrs["Authorization"] == "Bearer secret"
    assert "secret" not in request.full_url and "Authorization" not in request.headers

"""The GitHub calls a review makes: list threads, find the last review, post a review.

Only the standard library is used. The token goes only in the ``Authorization`` header.
Any refusal or unexpected reply fails the check (:class:`ReviewError`): a listing that
could not be read is never taken for an empty one.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from devloop.review.model import (
    BOT_LOGIN,
    MAX_COMMENTS_PER_REVIEW,
    Baseline,
    Provider,
    ReviewError,
    Settings,
    ThreadMark,
)
from devloop.review.threads import mark_for, retired_threads

GITHUB_API = "https://api.github.com"
GITHUB_GRAPHQL = f"{GITHUB_API}/graphql"
TIMEOUT_SECONDS = 30
_NEXT = re.compile(r'<([^>]+)>;\s*rel="next"')
_SHA = re.compile(r"[0-9a-f]{40}")
#: This reviewer's summary marker; ``base`` is the base branch tip it reviewed against.
_SUMMARY = re.compile(
    r"<!-- ([a-z]+-review) findings=\d+ posted=\d+ not_posted=\d+ unreviewed=(\d+) "
    r"base=([0-9a-f]{40})(?: since=[0-9a-f]{40})? -->\s*\Z"
)
_ANY_SUMMARY = re.compile(r"<!-- ([a-z]+-review) findings=")


@dataclass(frozen=True)
class Reply:
    """One GitHub API reply: its status, its JSON body, and the next page's URL."""

    status: int
    data: Any
    next_url: str | None = None


#: Send one request: method, URL, token and JSON body (or ``None``).
Http = Callable[[str, str, str, object], Reply]


def http(method: str, url: str, token: str, body: object = None) -> Reply:
    """Send one request to the GitHub API and return its reply; a 4xx or 5xx is a reply too.

    Raises:
        ReviewError: GitHub could not be reached, or its reply is not JSON.

    """
    request = urllib.request.Request(
        url,
        data=None if body is None else json.dumps(body).encode(),
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    request.add_unredirected_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            status, raw, link = response.status, response.read(), response.headers.get("Link")
    except urllib.error.HTTPError as error:
        status, raw, link = error.code, error.read(), None
    except (urllib.error.URLError, OSError) as error:
        raise ReviewError(f"GitHub could not be reached ({type(error).__name__})") from None
    try:
        data = json.loads(raw) if raw else None
    except ValueError:
        raise ReviewError(f"GitHub's reply ({status}) is not JSON") from None
    found = _NEXT.search(link or "")
    return Reply(status, data, found.group(1) if found else None)


def _get_all(
    settings: Settings, url: str, what: str, send: Http = http, pages: int = 50
) -> list[Any]:
    """Return every item of a paginated listing, failing the check on any refusal."""
    items: list[Any] = []
    next_url: str | None = url
    while next_url is not None and pages:
        reply = send("GET", next_url, settings.github_token, None)
        if reply.status >= 400:
            raise ReviewError(f"GitHub refused to list {what} ({reply.status})")
        if not isinstance(reply.data, list):
            raise ReviewError(f"GitHub's listing of {what} has an unexpected shape")
        items += reply.data
        next_url, pages = reply.next_url, pages - 1
    return items


def _pr_url(settings: Settings, tail: str) -> str:
    """Return the REST URL of the pull request's ``tail`` listing."""
    return f"{GITHUB_API}/repos/{settings.repository}/pulls/{settings.pr_number}/{tail}"


_THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $after) {
        nodes { isResolved comments(first: 1) { nodes { databaseId } } }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
"""


def open_thread_comment_ids(settings: Settings, send: Http = http) -> set[int]:
    """Return the first-comment ids of the pull request's unresolved review threads.

    The REST API does not say whether a thread is resolved, so this asks GraphQL.
    """
    owner, _, name = settings.repository.partition("/")
    found: set[int] = set()
    after: str | None = None
    while True:
        variables = {"owner": owner, "name": name, "number": int(settings.pr_number)}
        query = {"query": _THREADS_QUERY, "variables": {**variables, "after": after}}
        reply = send("POST", GITHUB_GRAPHQL, settings.github_token, query)
        if reply.status >= 400:
            raise ReviewError(f"GitHub refused to list review threads ({reply.status})")
        try:
            threads = reply.data["data"]["repository"]["pullRequest"]["reviewThreads"]
        except (KeyError, TypeError):
            raise ReviewError("GitHub's review-thread listing has an unexpected shape") from None
        for thread in threads.get("nodes") or []:
            if thread.get("isResolved"):
                continue
            for comment in (thread.get("comments") or {}).get("nodes") or []:
                if isinstance(comment.get("databaseId"), int):
                    found.add(comment["databaseId"])
        page = threads.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            return found
        after = page.get("endCursor")


def known_threads(
    settings: Settings, provider: Provider, reviewers: Mapping[str, str], send: Http = http
) -> set[ThreadMark]:
    """Return this reviewer's earlier threads, and the other reviewers' open threads.

    Only comments by the workflows' own bot carry trusted markers, so a marker someone
    else writes cannot hide a finding. An own thread a repository member marked fixed (an
    ``accepted:`` reply), or with no comment for ``OWN_THREAD_DAYS`` days, is left out, so
    a regression gets a new thread. Another reviewer's thread counts only while unresolved.
    ``reviewers`` maps each devloop reviewer's marker prefix to its name.
    """
    url = f"{_pr_url(settings, 'comments')}?per_page=100"
    comments = _get_all(settings, url, "review comments", send)
    retired = retired_threads(comments, datetime.now(UTC))
    marks: set[ThreadMark] = set()
    others: dict[int, list[ThreadMark]] = {}
    for comment in comments:
        for mark in mark_for(provider, comment, reviewers):
            if mark.reviewer == provider.name:
                if mark.comment_id not in retired:
                    marks.add(mark)
            elif isinstance(comment.get("id"), int):
                others.setdefault(comment["id"], []).append(mark)
    if others:
        unresolved = open_thread_comment_ids(settings, send)
        marks.update(m for cid, found in others.items() if cid in unresolved for m in found)
    return marks


def post_review(
    settings: Settings,
    provider: Provider,
    body: str,
    comments: Sequence[Mapping[str, Any]],
    send: Http = http,
) -> None:
    """Post the review on the head commit, with ``comments`` as inline threads.

    Comments go in batches of :data:`MAX_COMMENTS_PER_REVIEW`, one review per batch, so
    no finding is dropped. The event is ``COMMENT``: a workflow's ``GITHUB_TOKEN`` cannot
    approve pull requests, and the gate is the required check plus resolved threads.
    """
    batches = [
        list(comments[start : start + MAX_COMMENTS_PER_REVIEW])
        for start in range(0, len(comments), MAX_COMMENTS_PER_REVIEW)
    ] or [[]]
    for number, batch in enumerate(batches, start=1):
        text = (
            body if number == 1 else f"{provider.header}: findings, part {number} of {len(batches)}"
        )
        payload = {"commit_id": settings.head_sha, "event": "COMMENT", "body": text}
        reply = send(
            "POST",
            _pr_url(settings, "reviews"),
            settings.github_token,
            {**payload, "comments": batch},
        )
        if reply.status >= 400:
            raise ReviewError(f"GitHub refused the review ({reply.status})")


def previous_review(settings: Settings, provider: Provider, send: Http = http) -> Baseline | None:
    """Return the last head this reviewer completed on this pull request, or ``None``.

    That is the newest review by the workflows' bot carrying this reviewer's summary
    marker, when the marker says every file was reviewed and records its base, and the
    newest ``status_context`` status on that head is the bot's ``success``. The author
    can write neither; anything short of all of it gives ``None``, so a full review.

    Raises:
        ReviewError: GitHub refused a listing; the check fails.

    """
    own: list[tuple[int, str, str]] = []
    for review in _get_all(
        settings, f"{_pr_url(settings, 'reviews')}?per_page=100", "reviews", send
    ):
        body = str(review.get("body") or "")
        tag = _ANY_SUMMARY.search(body)
        by_bot = (review.get("user") or {}).get("login") == BOT_LOGIN
        if by_bot and tag and tag.group(1) == provider.summary_marker:
            rid = review.get("id")
            own.append((rid if isinstance(rid, int) else -1, str(review.get("commit_id")), body))
    if not own:
        return None
    _, head, body = max(own, key=lambda item: item[0])
    found = _SUMMARY.search(body)
    if not _SHA.fullmatch(head) or found is None or found.group(1) != provider.summary_marker:
        return None
    if found.group(2) != "0":  # the newest review was incomplete
        return None
    url = f"{GITHUB_API}/repos/{settings.repository}/commits/{head}/statuses?per_page=100"
    statuses = _get_all(settings, url, "commit statuses", send, pages=1)
    latest = next((s for s in statuses if s.get("context") == provider.status_context), None)
    if latest is None or latest.get("state") != "success":
        return None
    if (latest.get("creator") or {}).get("login") != BOT_LOGIN:
        return None
    return Baseline(head, found.group(3))

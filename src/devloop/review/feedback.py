"""Collect what maintainers did with review findings into the feedback store.

``devloop feedback`` reads recently merged pull requests and writes ``review.feedback``:
recent accepted and rejected findings become prompt examples for the reviewers, and a
finding rejected :data:`REJECTION_THRESHOLD` times or more is suppressed (listed in the
summary, never a thread; a P1 is never suppressed). Ported from gtm_toolkit's
``scripts/review_feedback.py``.

How a resolved thread is read:

- Only threads the workflows' bot opened with a devloop reviewer's marker, or a review
  app's own threads (:data:`~devloop.review.model.OTHER_REVIEW_APPS`), are findings. A
  marker anyone else writes is ignored.
- Only replies from a repository owner, member or collaborator count; a fork author's,
  a bot's and other users' never do.
- A counted reply labels the finding only when it starts with one of ``labels.prefixes``
  and a colon (``accepted: fixed in abc123``, any case); the latest labelled reply wins.
  Anything else, and a thread resolved with no labelled reply, is unknown and skipped.
- An unresolved thread is skipped.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from devloop.review.github import GITHUB_API, GITHUB_GRAPHQL, Http, Reply, http
from devloop.review.model import (
    BOT_LOGIN,
    OTHER_REVIEW_APPS,
    TRUSTED_ASSOCIATIONS,
    ReviewError,
)

#: The workflows' bot, as REST and GraphQL name it.
BOT_LOGINS = frozenset({BOT_LOGIN, BOT_LOGIN.removesuffix("[bot]")})
#: Review apps by their REST and GraphQL logins.
APP_NAMES = {
    **OTHER_REVIEW_APPS,
    **{k.removesuffix("[bot]"): v for k, v in OTHER_REVIEW_APPS.items()},
}
#: A review app's severity badge (``alt="P2"``) and its bold finding title.
APP_SEVERITY = re.compile(r'alt="(P[123])"')
APP_TITLE = re.compile(r"\*\*([^*\n]+?)\*\*")
#: A devloop thread marker: the older title-based key, or the severity-and-file key.
MARKER_RE = re.compile(r"<!-- ([a-z][a-z0-9-]*)-finding:([0-9a-f]{16})( line=\d+)? -->")
#: The thread heading, ``**P2 — title**`` (``· _D4 name_`` with dimensions), or the
#: older ``**P2 (D4) — title**``.
HEADING_RE = re.compile(r"^\*\*(P[123])(?: \((D\d)\))? — (.+?)\*\*(?: · _(D\d) [^_\n]*_)?", re.M)
DAYS_BACK = 30
MAX_PRS = 50
REJECTION_THRESHOLD = 3
#: The most records the store keeps, newest last.
MAX_RECORDS = 5000
#: Waits before each retry of a GitHub call that failed transiently.
RETRY_WAITS = (15.0, 30.0)
TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})
EMPTY_STORE: dict[str, Any] = {"version": 2, "records": [], "suppressed": []}

_COMMENT_FIELDS = "body url createdAt authorAssociation author { login }"
THREADS_QUERY = f"""
query($owner: String!, $repo: String!, $pr: Int!, $after: String) {{
  repository(owner: $owner, name: $repo) {{
    pullRequest(number: $pr) {{
      author {{ login }}
      reviewThreads(first: 100, after: $after) {{
        pageInfo {{ hasNextPage endCursor }}
        nodes {{
          id isResolved path
          comments(first: 100) {{
            pageInfo {{ hasNextPage endCursor }}
            nodes {{ {_COMMENT_FIELDS} }}
          }}
        }}
      }}
    }}
  }}
}}
"""
COMMENTS_QUERY = f"""
query($id: ID!, $after: String) {{
  node(id: $id) {{
    ... on PullRequestReviewThread {{
      comments(first: 100, after: $after) {{
        pageInfo {{ hasNextPage endCursor }}
        nodes {{ {_COMMENT_FIELDS} }}
      }}
    }}
  }}
}}
"""

Sleep = Callable[[float], None]


class FeedbackError(RuntimeError):
    """GitHub refused or failed a read; nothing is written from a partial read."""


def request(
    method: str, url: str, token: str, body: object = None, *, send: Http = http, sleep: Sleep
) -> Reply:
    """Return a GitHub reply, retried after 15 and 30 s on a 429, a 5xx or a lost connection.

    Raises:
        FeedbackError: the last attempt still failed.

    """
    for attempt in range(len(RETRY_WAITS) + 1):
        last = attempt == len(RETRY_WAITS)
        try:
            reply = send(method, url, token, body)
        except ReviewError as error:  # GitHub unreachable, or a reply that is not JSON
            if last:
                raise FeedbackError(f"GitHub could not be read: {error}") from None
        else:
            if reply.status < 400:
                return reply
            if reply.status not in TRANSIENT_STATUS or last:
                raise FeedbackError(f"GitHub refused {method} {url.split('?')[0]} ({reply.status})")
        sleep(RETRY_WAITS[attempt])
    raise AssertionError("unreachable: the last attempt returns or raises")


def graphql(
    token: str, query: str, variables: Mapping[str, Any], *, send: Http = http, sleep: Sleep
) -> dict[str, Any]:
    """Return the ``data`` of a GraphQL query.

    Raises:
        FeedbackError: GitHub refused it or answered with GraphQL errors.

    """
    body = {"query": query, "variables": dict(variables)}
    reply = request("POST", GITHUB_GRAPHQL, token, body, send=send, sleep=sleep)
    data = reply.data if isinstance(reply.data, dict) else {}
    if data.get("errors") or not isinstance(data.get("data"), dict):
        raise FeedbackError(f"GraphQL errors: {data.get('errors')}")
    found: dict[str, Any] = data["data"]
    return found


def recent_merged_prs(
    token: str, repo: str, days: int = DAYS_BACK, *, send: Http = http, sleep: Sleep
) -> list[int]:
    """Return the numbers of pull requests merged and updated in the last ``days`` days.

    The listing is newest-updated first, so paging stops at the first older one; at most
    :data:`MAX_PRS` are returned.
    """
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    numbers: list[int] = []
    url: str | None = (
        f"{GITHUB_API}/repos/{repo}/pulls?state=closed&sort=updated&direction=desc&per_page=100"
    )
    while url and len(numbers) < MAX_PRS:
        reply = request("GET", url, token, send=send, sleep=sleep)
        page = reply.data if isinstance(reply.data, list) else []
        if not page:
            break
        for pr in page:
            if str(pr.get("updated_at", "")) < since:
                return numbers
            if pr.get("merged_at"):
                numbers.append(int(pr["number"]))
                if len(numbers) >= MAX_PRS:
                    return numbers
        url = reply.next_url
    return numbers


def pr_threads(
    token: str, owner: str, name: str, number: int, *, send: Http = http, sleep: Sleep
) -> list[dict[str, Any]]:
    """Return every review thread of a pull request, each with all its comments."""
    threads: list[dict[str, Any]] = []
    after: str | None = None
    while True:
        variables = {"owner": owner, "repo": name, "pr": number, "after": after}
        data = graphql(token, THREADS_QUERY, variables, send=send, sleep=sleep)
        pr = (data.get("repository") or {}).get("pullRequest")
        if not pr:
            return threads
        page = pr["reviewThreads"]
        for thread in page["nodes"]:
            comments = thread["comments"]
            nodes, info = list(comments["nodes"]), comments["pageInfo"]
            while info.get("hasNextPage"):
                more = graphql(
                    token,
                    COMMENTS_QUERY,
                    {"id": thread["id"], "after": info["endCursor"]},
                    send=send,
                    sleep=sleep,
                )["node"]["comments"]
                nodes += more["nodes"]
                info = more["pageInfo"]
            threads.append({**thread, "comments": nodes})
        if not page["pageInfo"].get("hasNextPage"):
            return threads
        after = page["pageInfo"]["endCursor"]


def title_fingerprint(severity: str, path: str, title: str) -> str:
    """Return the title-based key the reviewers' suppression matches (``legacy_fingerprint``)."""
    normalised = " ".join(title.lower().split())
    return hashlib.sha256(f"{severity}|{path}|{normalised}".encode()).hexdigest()[:16]


def counts(comment: Mapping[str, Any]) -> bool:
    """Return whether a reply counts: a repository owner, member or collaborator, not a bot."""
    login = str((comment.get("author") or {}).get("login") or "")
    if not login or login in BOT_LOGINS or login.endswith("[bot]"):
        return False
    return comment.get("authorAssociation") in TRUSTED_ASSOCIATIONS


def label(comment: Mapping[str, Any], prefixes: Sequence[str]) -> str | None:
    """Return the label a reply starts with (one of ``prefixes`` and a colon), else ``None``."""
    pattern = r"^\s*(" + "|".join(re.escape(p) for p in prefixes) + r")\s*:"
    found = re.match(pattern, str(comment.get("body") or ""), re.IGNORECASE)
    return found.group(1).lower() if found else None


def _app_finding(body: str) -> tuple[str, str] | None:
    """Return the severity and title of a review app's finding comment, or ``None``."""
    severity, title = APP_SEVERITY.search(body), APP_TITLE.search(body)
    if severity is None or title is None:
        return None
    return severity.group(1), title.group(1).strip().rstrip(".")


def finding(thread: Mapping[str, Any], reviewers: Mapping[str, str]) -> dict[str, str] | None:
    """Return what a thread's first comment says it found, or ``None`` when it is no finding.

    ``reviewers`` maps each devloop reviewer's marker prefix (``gemini-finding``) to its
    name. Only the bot's marker, or a review app's own login, makes a finding.
    """
    comments = thread.get("comments") or []
    if not comments:
        return None
    first = comments[0]
    login = (first.get("author") or {}).get("login")
    body = str(first.get("body") or "").replace("​", "")
    path = str(thread.get("path") or "")
    if login in BOT_LOGINS:
        marker, heading = MARKER_RE.search(body), HEADING_RE.search(body)
        if marker is None or heading is None:
            return None
        reviewer = reviewers.get(f"{marker.group(1)}-finding")
        if reviewer is None:
            return None
        severity, title = heading.group(1), heading.group(3).strip()
        dimension = heading.group(2) or heading.group(4) or "unknown"
        key = title_fingerprint(severity, path, title) if marker.group(3) else marker.group(2)
    elif login in APP_NAMES:
        found = _app_finding(body)
        if found is None:
            return None
        reviewer, (severity, title), dimension = APP_NAMES[str(login)], found, "unknown"
        key = title_fingerprint(severity, path, title)
    else:
        return None
    return {
        "reviewer": reviewer,
        "fingerprint": key,
        "severity": severity,
        "dimension": dimension,
        "title": title,
        "path": path,
        "url": str(first.get("url") or ""),
        "thread": str(thread.get("id") or ""),
    }


def labels_of(thread: Mapping[str, Any], prefixes: Sequence[str]) -> list[tuple[str, str]]:
    """Return each counted labelled reply of a thread: its label and when it was written."""
    found = []
    for comment in (thread.get("comments") or [])[1:]:
        tag = label(comment, prefixes) if counts(comment) else None
        if tag is not None:
            found.append((tag, str(comment.get("createdAt") or "")))
    return found


def classify_thread(
    thread: Mapping[str, Any], reviewers: Mapping[str, str], prefixes: Sequence[str]
) -> dict[str, Any] | None:
    """Return the feedback record for one resolved, labelled finding thread, or ``None``."""
    if not thread.get("isResolved"):
        return None
    found = finding(thread, reviewers)
    labelled = labels_of(thread, prefixes)
    if found is None or not labelled:
        return None  # not a finding, or unknown: never used
    outcome, when = labelled[-1]
    return {**found, "outcome": outcome, "labelled_at": when}


def suppressions(
    records: Sequence[Mapping[str, Any]], names: Sequence[str]
) -> list[dict[str, Any]]:
    """Return findings of the devloop reviewers ``names`` rejected often enough, never a P1.

    A review app's records feed the lessons only; it is not tuned from here.
    """
    rejected = [
        r
        for r in records
        if r.get("outcome") == "rejected"
        and r.get("severity") != "P1"
        and r.get("reviewer") in names
    ]
    tally = Counter((r["reviewer"], r["fingerprint"]) for r in rejected)
    last = {(r["reviewer"], r["fingerprint"]): r.get("url", "") for r in rejected}
    return [
        {"reviewer": reviewer, "fingerprint": fp, "rejections": n, "url": last[(reviewer, fp)]}
        for (reviewer, fp), n in sorted(tally.items())
        if n >= REJECTION_THRESHOLD
    ]


def record_key(record: Mapping[str, Any]) -> tuple[str, int, str]:
    """Return a record's identity: the reviewer, the pull request and the review thread.

    A record from before thread ids were stored falls back to its fingerprint.
    """
    thread = str(record.get("thread") or "") or f"fingerprint:{record.get('fingerprint')}"
    return (str(record.get("reviewer")), int(record.get("pr") or 0), thread)


def collect(
    token: str,
    repo: str,
    existing: Mapping[str, Any],
    reviewers: Mapping[str, str],
    prefixes: Sequence[str],
    *,
    send: Http = http,
    sleep: Sleep = time.sleep,
) -> dict[str, Any]:
    """Return the store ``existing`` updated with feedback from recently merged pull requests.

    A thread read again replaces its saved record, so a changed label is believed; records
    of pull requests outside the window are kept.

    Raises:
        FeedbackError: a read failed; the caller writes nothing.

    """
    merged: dict[tuple[str, int, str], dict[str, Any]] = {
        record_key(r): r
        for r in existing.get("records") or []
        if isinstance(r, dict) and "reviewer" in r
    }
    for record in _records(token, repo, reviewers, prefixes, send, sleep):
        if record.get("thread"):  # a record saved before thread ids is keyed by fingerprint
            merged.pop(record_key({**record, "thread": ""}), None)
        merged[record_key(record)] = record
    records = list(merged.values())[-MAX_RECORDS:]
    return {
        "version": 2,
        "records": records,
        "suppressed": suppressions(records, sorted(set(reviewers.values()))),
    }


def _records(
    token: str,
    repo: str,
    reviewers: Mapping[str, str],
    prefixes: Sequence[str],
    send: Http,
    sleep: Sleep,
) -> Iterator[dict[str, Any]]:
    """Yield feedback records from recently merged pull requests, one per labelled thread."""
    owner, name = repo.split("/", 1)
    for number in recent_merged_prs(token, repo, send=send, sleep=sleep):
        for thread in pr_threads(token, owner, name, number, send=send, sleep=sleep):
            record = classify_thread(thread, reviewers, prefixes)
            if record is not None:
                yield {**record, "pr": number}


def load_store(path: Path) -> dict[str, Any]:
    """Return the store at ``path``, or an empty one when it is missing.

    Raises:
        FeedbackError: the file exists but is not a JSON object, so it is never overwritten
            with a store that has lost its records.

    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return dict(EMPTY_STORE)
    except (OSError, json.JSONDecodeError) as error:
        raise FeedbackError(f"{path} cannot be read: {error}") from None
    if not isinstance(data, dict):
        raise FeedbackError(f"{path} is not a JSON object")
    return data


def main(root: Path, env: Mapping[str, str]) -> int:
    """Update the store ``review.feedback`` names; 0 when written, 1 on a failed read, 2 on setup.

    Nothing is written when any read fails, so a store never loses records to a partial
    read.
    """
    import sys

    from devloop.config import ConfigError, load_config
    from devloop.review.command import marker_names

    token, repo = env.get("GITHUB_TOKEN", ""), env.get("GITHUB_REPOSITORY", "")
    try:
        config = load_config(root / "devloop.toml")
    except ConfigError as error:
        print(f"devloop feedback: {error}", file=sys.stderr)
        return 2
    if not config.review.feedback or not token or "/" not in repo:
        print(
            "devloop feedback: set review.feedback in devloop.toml, and GITHUB_TOKEN and "
            "GITHUB_REPOSITORY (owner/name)",
            file=sys.stderr,
        )
        return 2
    path = root / config.review.feedback
    try:
        store = collect(token, repo, load_store(path), marker_names(config), config.labels.prefixes)
    except FeedbackError as error:
        print(f"devloop feedback: {error}; nothing written", file=sys.stderr)
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, indent=2) + "\n", encoding="utf-8")
    held = len(store["suppressed"])
    print(f"Collected feedback: {len(store['records'])} records, {held} suppressed.")
    return 0

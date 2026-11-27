"""Read a pull request from GitHub, judge it by the gate's rules and post the status.

The rules are :mod:`devloop.gate.rules`, with the ``[gate]`` table the caller read.

Invariants:

1. No status is written unless the pull request is open, goes into the repository's
   default branch, and its status is due by :mod:`devloop.review.status_guard` (not a
   fork's pull request whose head a branch of the base repository also heads). The
   one exception is a ``failure`` when that last guard could not be read.
2. :func:`evaluate` is the only writer of ``pending``, and writes it only after the guards.
3. Whatever cannot be read is a ``failure`` on the head and exit code 1, never a pass
   and never an earlier success left standing. A pull request that cannot be read at
   all gets no status, only the failed run.
4. The changed files are read by the two commits, never by the pull request's number,
   and the commit judged is the commit posted on. A list that does not cover the pull
   request's commits and files counts as a control change.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from typing import Any, Protocol
from urllib.parse import quote

from devloop.config import Gate
from devloop.gate import rules
from devloop.review import feedback as fb
from devloop.review import status_guard as guard

#: A list from the API is read to at most this many pages of 100.
MAX_PAGES = 30


class GitHub(Protocol):
    """What the gate needs of GitHub: reads of the API, and one status write."""

    def get(self, path: str) -> object:
        """Return the decoded JSON of ``GET <api>/<path>``; raises ``FeedbackError``."""
        ...

    def post(self, sha: str, state: str, description: str) -> None:
        """Post the gate's status on ``sha``; raises ``FeedbackError``."""
        ...


def _field(record: object, *keys: str) -> object:
    """Return the value at ``keys`` inside nested records, or None when one is missing."""
    for key in keys:
        record = record.get(key) if isinstance(record, Mapping) else None
    return record


def _records(value: object) -> list[dict[str, Any]]:
    """Return ``value`` as a list of records; raises when it is anything else."""
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise fb.FeedbackError("a list from the API is not a list of records")
    return value


def _pages(hub: GitHub, path: str, key: str | None = None) -> list[dict[str, Any]]:
    """Return every item of a paged list, read to its last page; raises when it does not end."""
    items: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        answer = hub.get(f"{path}{'&' if '?' in path else '?'}per_page=100&page={page}")
        if key is not None:
            if not isinstance(answer, dict):
                raise fb.FeedbackError("an answer from the API is not a record")
            answer = answer.get(key)
        batch = _records(answer)
        items.extend(batch)
        if len(batch) < 100:
            return items
    raise fb.FeedbackError("a list from the API has more pages than the gate reads")


def _tree(hub: GitHub, repo: str, sha: str) -> dict[str, str]:
    """Return every path of the tree of ``sha`` with its git mode; raises when it is not whole."""
    tree = hub.get(f"repos/{repo}/git/trees/{sha}?recursive=1")
    if not isinstance(tree, dict) or tree.get("truncated") is not False:
        raise fb.FeedbackError("a tree could not be read whole")
    return {str(entry.get("path")): str(entry.get("mode")) for entry in _records(tree.get("tree"))}


def _lane_of(hub: GitHub, repo: str, pull: dict[str, Any], gate: Gate) -> tuple[rules.Lane, int]:
    """Return the lane of the head of ``pull`` as it was read, and how many files it changes.

    The files are read by the two commits (invariant 4): a list read by the pull
    request's number is the list of whatever head it has at that moment. The comparison
    answers every file at once, up to 300; a longer list falls short of the count the
    pull request reports and so is not complete. It must also cover as many commits as
    the pull request has: a comparison with another base is not this pull request's
    list. A list that is not complete is strict and flagged, never light.
    """
    sha, base = str(pull["head"]["sha"]), str(pull["base"]["sha"])
    answer = hub.get(f"repos/{repo}/compare/{base}...{sha}?per_page=1&page=1")
    if not isinstance(answer, dict):
        raise fb.FeedbackError("the comparison of the two commits is not a record")
    counts = (answer.get("total_commits"), pull["commits"])
    if counts[0] != counts[1] or any(type(count) is not int for count in counts):
        return rules.Lane(False, True, "the list of changed files is not complete"), 0
    modes = _tree(hub, repo, sha)
    files = [
        rules.ChangedFile(
            path=str(entry.get("filename") or ""),
            status=str(entry.get("status") or ""),
            previous=entry.get("previous_filename"),
            mode=modes.get(str(entry.get("filename") or "")),
        )
        for entry in _records(answer.get("files"))
    ]
    known = _tree(hub, repo, base)
    found = rules.lane(files, expected=int(pull["changed_files"]), known=known, gate=gate)
    return found, len(files)


def _verdict(
    hub: GitHub, repo: str, sha: str, found: rules.Lane, trusted: bool, gate: Gate
) -> tuple[rules.Verdict, list[str]]:
    """Return the verdict for ``sha`` from the reviews as they stand now, and a line per review."""
    statuses = _pages(hub, f"repos/{repo}/commits/{sha}/statuses")
    needed = {context: rules.status_signal(statuses, context) for context in gate.statuses}
    for check in gate.checks:
        query = f"check_name={quote(check.name, safe='')}&app_id={check.app}"
        runs = _pages(hub, f"repos/{repo}/commits/{sha}/check-runs?{query}", "check_runs")
        needed[check.name] = rules.check_signal(runs, check, trusted=trusted)
    blocking = {context: rules.status_signal(statuses, context) for context in gate.light_blocks}
    lines = [f"- {name}: {signal.value}" for name, signal in {**blocking, **needed}.items()]
    return rules.decide(found, trusted=trusted, needed=needed, blocking=blocking), lines


def evaluate(hub: GitHub, repo: str, number: int, gate: Gate) -> int:
    """Judge pull request ``number`` of ``repo`` and post the verdict; return the exit code.

    0 when a verdict was posted, or none is due (a closed pull request, another base
    branch, a fork's head that a branch of ``repo`` also heads, a head that moved
    meanwhile). 1 when the gate could not check (invariant 3). After posting, the
    reviews are read once more and the verdict posted again if it changed. That narrows
    the time in which a slow run can leave an older answer over a newer one; the
    workflow closes it by running one evaluation of a pull request at a time.

    Raises:
        FeedbackError: the pull request could not be read, or a failure could not be
            posted.

    """
    pull = hub.get(f"repos/{repo}/pulls/{number}")
    if not isinstance(pull, dict):
        raise fb.FeedbackError("the pull request could not be read")
    if pull.get("state") != "open":
        print(f"Pull request {number} is not open; nothing posted.")
        return 0
    default, sha = _field(pull, "base", "repo", "default_branch"), _field(pull, "head", "sha")
    if not isinstance(default, str) or not default or not isinstance(sha, str) or not sha:
        raise fb.FeedbackError("the pull request names no head commit or no default branch")
    if _field(pull, "base", "ref") != default:
        print(f"Pull request {number} does not go into {default}; nothing posted.")
        return 0
    posted: rules.Verdict | None = None
    try:
        where = f"repos/{repo}/commits/{sha}/branches-where-head"
        if not guard.head_of(pull, number, lambda _: hub.get(where)).due:
            print(guard.notice(gate.context, number, sha))
            return 0
        trusted = rules.is_trusted(pull, repo, gate.trusted_bots)
        hub.post(sha, "pending", "Checking the lane and the reviews")
        found, count = _lane_of(hub, repo, pull, gate)
        for _ in range(2):
            verdict, lines = _verdict(hub, repo, sha, found, trusted, gate)
            again = hub.get(f"repos/{repo}/pulls/{number}")
            if not isinstance(again, dict):
                raise fb.FeedbackError("the pull request could not be read again")
            if _field(again, "head", "sha") != sha:
                print("The head moved while the gate was reading; its own run judges it.")
                return 0
            if verdict == posted:
                break
            hub.post(sha, verdict.state, verdict.description)
            posted = verdict
            print(f"{gate.context}: {verdict.state}: {verdict.description} ({count} files)")
            print("\n".join(lines))
    except Exception as error:
        # Anything unexpected is "could not check": a failure on the head and a failed run.
        hub.post(sha, "failure", "The merge gate could not check this head; re-run it")
        # Only devloop's own errors are quoted: another error's text may hold what was read.
        detail = f": {error}" if isinstance(error, fb.FeedbackError) else ""
        print(f"devloop gate: could not check: {type(error).__name__}{detail}", file=sys.stderr)
        return 1
    return 0

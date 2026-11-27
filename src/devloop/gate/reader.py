"""``devloop gate``: read a pull request from GitHub, judge it and post the gate's status.

The rules are :mod:`devloop.gate.rules`, with the ``[gate]`` table of the ``devloop.toml``
in the folder the command runs in. The workflow checks out the default branch there, so
a pull request never supplies its own rules.

Invariants:

1. No status is written unless the pull request is open, goes into the repository's
   default branch, and its status is due by :mod:`devloop.review.status_guard` (not a
   fork's pull request whose head a branch of the base repository also heads). The
   one exception is a ``failure`` when one of the three could not be read: a record
   without its state, base branch or default branch is "could not check", never
   "nothing is due".
2. This command is the only writer of ``pending``, and writes it only after the guards.
3. Whatever cannot be read is a ``failure`` on the head and exit code 1, never a pass
   and never an earlier success left standing. A pull request that cannot be read at
   all gets no status, only the failed run.
4. The changed files are read by the two commits, never by the pull request's number,
   and the commit judged is the commit posted on. A list that does not cover the pull
   request's commits and files counts as a control change.
"""

from __future__ import annotations

import re
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

from devloop.config import CONFIG_FILE, ConfigError, Gate, load_config
from devloop.gate import rules
from devloop.review import feedback as fb
from devloop.review import status_guard as guard
from devloop.review.github import GITHUB_API, Http, http
from devloop.review.labels import write_outputs

#: A list from the API is read to at most this many pages of 100.
MAX_PAGES = 30
_REPO = re.compile(r"[\w.-]+/[\w.-]+")


class GitHub(Protocol):
    """What the gate needs of GitHub: reads of the API, and one status write."""

    def get(self, path: str) -> object:
        """Return the decoded JSON of ``GET <api>/<path>``; raises ``FeedbackError``."""
        ...

    def post(self, sha: str, state: str, description: str) -> None:
        """Post the gate's status on ``sha``; raises ``FeedbackError``."""
        ...


@dataclass(frozen=True)
class Api:
    """GitHub's REST API with the token of the job, retried like every devloop call."""

    token: str
    repo: str
    context: str
    run_url: str = ""
    send: Http = http
    sleep: Callable[[float], None] = time.sleep

    def get(self, path: str) -> object:
        """Return the decoded JSON of ``GET <api>/<path>``."""
        url = f"{GITHUB_API}/{path}"
        return fb.request("GET", url, self.token, send=self.send, sleep=self.sleep).data

    def post(self, sha: str, state: str, description: str) -> None:
        """Post the gate's status on ``sha``, linked to the run when its URL is known."""
        body = {"state": state, "context": self.context, "description": description[:140]}
        if self.run_url:
            body["target_url"] = self.run_url
        url = f"{GITHUB_API}/repos/{self.repo}/statuses/{sha}"
        fb.request("POST", url, self.token, body, send=self.send, sleep=self.sleep)


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


def _changed(entry: Mapping[str, object], modes: Mapping[str, str]) -> rules.ChangedFile:
    """Return one record of the comparison as a changed file; raises when it is not text."""
    name, status, previous = (entry.get(k) for k in ("filename", "status", "previous_filename"))
    if (
        not isinstance(name, str)
        or not name
        or not isinstance(status, str)
        or not (previous is None or isinstance(previous, str))
    ):
        raise fb.FeedbackError("a changed file's name or kind of change is not text")
    return rules.ChangedFile(path=name, status=status, previous=previous, mode=modes.get(name))


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
    files = [_changed(entry, modes) for entry in _records(answer.get("files"))]
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
        FeedbackError: the pull request could not be read or names no head commit, or a
            failure could not be posted.

    """
    pull = hub.get(f"repos/{repo}/pulls/{number}")
    if not isinstance(pull, dict):
        raise fb.FeedbackError("the pull request could not be read")
    sha = _field(pull, "head", "sha")
    if not isinstance(sha, str) or not sha:
        raise fb.FeedbackError("the pull request names no head commit")
    posted: rules.Verdict | None = None
    try:
        state, ref = _field(pull, "state"), _field(pull, "base", "ref")
        default = _field(pull, "base", "repo", "default_branch")
        if not all(isinstance(value, str) and value for value in (state, ref, default)):
            raise fb.FeedbackError("the pull request names no state, base or default branch")
        if state != "open":
            print(f"Pull request {number} is not open; nothing posted.")
            return 0
        if ref != default:
            print(f"Pull request {number} does not go into {default}; nothing posted.")
            return 0
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
            now = _field(again, "head", "sha")
            if not isinstance(now, str) or not now:
                raise fb.FeedbackError("the pull request, read again, names no head commit")
            if now != sha:
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


def main(root: Path, repo: str, number: int, run_url: str, env: Mapping[str, str]) -> int:
    """Run the gate for one pull request; 2 when it cannot start, 1 when it could not check.

    The status's name is written to ``$GITHUB_OUTPUT`` as ``context`` before anything is
    read, so the workflow can report a run that broke under the same name.
    """
    try:
        gate = rules.rules(load_config(root / CONFIG_FILE))
    except ConfigError as error:
        print(f"devloop gate: {error}", file=sys.stderr)
        return 2
    token = env.get("GITHUB_TOKEN", "")
    if not token or not _REPO.fullmatch(repo) or number <= 0:
        print(
            "devloop gate: GITHUB_TOKEN, --repo owner/name and a positive --pr are required",
            file=sys.stderr,
        )
        return 2
    write_outputs(env.get("GITHUB_OUTPUT", ""), context=gate.context)
    try:
        return evaluate(Api(token, repo, gate.context, run_url), repo, number, gate)
    except fb.FeedbackError as error:
        print(f"devloop gate: could not check: {error}", file=sys.stderr)
        return 1

"""Read a repository's settings from GitHub, for ``devloop doctor --repo``.

Read-only: the one call :class:`Reader` can make is a GET, through the client the other
commands use, with its retries. A read never fails the command: what GitHub refused, or
answered in another shape, is kept with its reason, so the setting is reported as "not
judged" and never taken for a value.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any
from urllib.parse import quote

from devloop.review import feedback as fb
from devloop.review.github import GITHUB_API, Http, Reply, http

#: What a refusal usually means, by status.
_HINTS = {
    403: "the token may not read this; it needs admin rights on the repository",
    404: "GitHub does not offer this for the repository, or the token may not see it",
    422: "GitHub does not offer this for this kind of repository",
}


@dataclass(frozen=True)
class Read:
    """One answer from GitHub: its data, or the reason there is none."""

    data: object = None
    reason: str = ""
    status: int = 200
    message: str = ""
    #: The branch has no classic protection: an answer, not a failed read.
    absent: bool = False


@dataclass(frozen=True)
class Reader:
    """GitHub's REST API, read only: the one call it can make is a GET."""

    token: str
    send: Http = http
    sleep: Callable[[float], None] = time.sleep

    def get(self, path: str) -> Read:
        """Return the answer to ``GET <api>/<path>``; a refusal is a ``Read`` with its reason."""
        last: list[Reply] = []

        def send(method: str, url: str, token: str, body: object) -> Reply:
            """Send through the real client, keeping the reply so a refusal can be named."""
            last.clear()
            last.append(self.send(method, url, token, body))
            return last[0]

        url = f"{GITHUB_API}/{path}"
        try:
            return Read(fb.request("GET", url, self.token, send=send, sleep=self.sleep).data)
        except fb.FeedbackError as error:
            if not last or last[0].status < 400:
                return Read(reason=str(error), status=0)
        status = last[0].status
        said = value_at(last[0].data, "message")
        message = " ".join(said.split())[:120] if isinstance(said, str) else ""
        reason = f"HTTP {status}" + (f" {message!r}" if message else "")
        hint = _HINTS.get(status)
        return Read(None, f"{reason}: {hint}" if hint else reason, status, message)


@dataclass(frozen=True)
class Facts:
    """Everything read from GitHub for one repository."""

    repo: Read
    protection: Read
    #: The active branch rulesets that cover the default branch.
    rulesets: tuple[Mapping[str, Any], ...]
    #: Why the rulesets are not known; empty when they were read.
    rulesets_reason: str
    actions: Read
    workflow: Read
    fork_approval: Read
    reporting: Read


def value_at(record: object, *keys: str) -> object:
    """Return the value at ``keys`` inside nested records, or None when one is missing."""
    for key in keys:
        record = record.get(key) if isinstance(record, Mapping) else None
    return record


def _covers(ruleset: Mapping[str, Any], branch: str) -> bool:
    """Return whether an active branch ruleset's conditions include ``branch``."""

    def named(key: str) -> bool:
        """Return whether a pattern of the ``key`` list names the branch."""
        patterns = value_at(ruleset, "conditions", "ref_name", key)
        return isinstance(patterns, list) and any(
            p in ("~ALL", "~DEFAULT_BRANCH") or fnmatchcase(f"refs/heads/{branch}", str(p))
            for p in patterns
        )

    live = ruleset.get("target") == "branch" and ruleset.get("enforcement") == "active"
    return live and named("include") and not named("exclude")


def _rulesets(reader: Reader, repo: str, branch: str) -> tuple[tuple[Mapping[str, Any], ...], str]:
    """Return the rulesets that cover ``branch``, or the reason they could not all be read."""
    listing = reader.get(f"repos/{repo}/rulesets?per_page=100")
    if listing.reason:
        return (), f"rulesets: {listing.reason}"
    items = listing.data
    if not isinstance(items, list) or len(items) >= 100:
        return (), "rulesets: GitHub's answer is not a list of fewer than 100 rulesets"
    found = []
    for item in items:
        number = value_at(item, "id")
        if not isinstance(number, int):
            return (), "rulesets: GitHub's list names a ruleset with no id"
        detail = reader.get(f"repos/{repo}/rulesets/{number}")
        if detail.reason:
            return (), f"ruleset {number}: {detail.reason}"
        data = detail.data
        if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
            return (), f"ruleset {number}: GitHub's answer has no rules"
        if _covers(data, branch):
            found.append(data)
    return tuple(found), ""


def read(reader: Reader, repo: str) -> Facts:
    """Read every setting of ``repo``; what cannot be read keeps its reason."""
    base = f"repos/{repo}"

    def record(path: str) -> Read:
        """Return the answer at ``path``, which must be a record to be read at all."""
        answer = reader.get(path)
        if answer.reason or isinstance(answer.data, Mapping):
            return answer
        return Read(reason="GitHub's answer is not a record", status=answer.status)

    info = record(base)
    branch = value_at(info.data, "default_branch")
    if isinstance(branch, str) and branch:
        protection = record(f"{base}/branches/{quote(branch, safe='')}/protection")
        if protection.status == 404 and protection.message == "Branch not protected":
            protection = Read(absent=True)
        rulesets, why = _rulesets(reader, repo, branch)
    else:
        unknown = f"the default branch is not known: {info.reason or 'GitHub did not name it'}"
        protection, rulesets, why = Read(reason=unknown), (), unknown
    return Facts(
        repo=info,
        protection=protection,
        rulesets=rulesets,
        rulesets_reason=why,
        actions=record(f"{base}/actions/permissions"),
        workflow=record(f"{base}/actions/permissions/workflow"),
        fork_approval=record(f"{base}/actions/permissions/fork-pr-contributor-approval"),
        reporting=record(f"{base}/private-vulnerability-reporting"),
    )

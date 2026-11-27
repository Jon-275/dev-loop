"""Decide whether a commit status may be written for a pull request's head.

A commit status belongs to a commit, not to a pull request. A pull request from a fork
can have, as its head, a commit that is also the head of a branch in the base repository:
anyone can point a fork's branch at that commit and open a pull request. A status written
for the fork's pull request would land on the shared commit and replace the one the
branch's own pull request earned, so a failing required status could turn green.

Invariants:

1. A status is due for a pull request's head unless the pull request comes from a fork
   (its head repository is not the base repository) AND the head commit is the head of
   at least one branch in the base repository. When it is not due, the run writes no
   pending and no verdict on that commit, says why in the log and succeeds.
2. The decision comes from the same read that gives the head commit (one
   ``GET pulls/{n}``), in the code that then posts, so the commit judged is the commit
   written. A deleted fork (``head.repo`` is null) is a fork.
3. A read that fails is "could not check" (:class:`~devloop.review.feedback.FeedbackError`):
   never due, never a pass. The caller fails the run. It may still post a failure, which
   can never grant a pass; pending and success are never written without this guard.
4. A pull request from the base repository itself is always due, and costs no extra read.

The limit: the guard reads the branches as they are when it runs. A branch moved onto the
fork's head afterwards shares statuses already written (see docs/security.md).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from devloop.review import feedback as fb
from devloop.review.github import GITHUB_API, Http, http


@dataclass(frozen=True)
class Head:
    """A pull request's head commit, and whether a status for it may be written there."""

    sha: str
    due: bool


def _full_name(side: object) -> str | None:
    """Return the repository name of a pull request's ``head`` or ``base``, or None."""
    repo = side.get("repo") if isinstance(side, Mapping) else None
    name = repo.get("full_name") if isinstance(repo, Mapping) else None
    return name if isinstance(name, str) and name else None


def read_head(repo: str, number: int, token: str, *, send: Http = http, sleep: fb.Sleep) -> Head:
    """Return pull request ``number``'s head and whether its status is due (invariant 1).

    One read for a pull request from ``repo`` itself; a fork's costs one more, the base
    repository's branches whose head is that commit.

    Raises:
        FeedbackError: GitHub could not be read, or its reply names no head commit or
            no base repository: the status could not be checked (invariant 3).

    """
    url = f"{GITHUB_API}/repos/{repo}/pulls/{number}"
    pull = fb.request("GET", url, token, send=send, sleep=sleep).data
    head = pull.get("head") if isinstance(pull, Mapping) else None
    sha = head.get("sha") if isinstance(head, Mapping) else None
    base = _full_name(pull.get("base")) if isinstance(pull, Mapping) else None
    if not isinstance(sha, str) or not sha or base is None:
        raise fb.FeedbackError(
            f"GitHub's reply for pull request {number} has no head commit or base repository"
        )
    if _full_name(head) == base:
        return Head(sha, due=True)
    url = f"{GITHUB_API}/repos/{repo}/commits/{sha}/branches-where-head"
    branches = fb.request("GET", url, token, send=send, sleep=sleep).data
    if not isinstance(branches, list):
        raise fb.FeedbackError(f"GitHub's list of the branches headed by {sha} is not a list")
    return Head(sha, due=not branches)


def notice(context: str, number: int, sha: str) -> str:
    """Return the log line that says why no ``context`` status was written on ``sha``.

    Built from the check's name, the number and the commit only: no text a pull request
    controls reaches the log command.
    """
    return (
        f"::notice title={context}::No status written for pull request #{number}: it comes "
        f"from a fork and its head {sha[:12]} is also the head of a branch in this "
        "repository, whose status it would replace"
    )

"""``devloop labels``: every review finding on a pull request has a labelled reply.

The ``Review labels`` commit status. The feedback loop (:mod:`devloop.review.feedback`)
learns only from threads a member answered with a label (``accepted:``, ``rejected:``);
an unlabelled thread is lost to the lessons, the prompt examples and the scorecard, so
this check makes the label part of finishing a thread. Ported from gtm_toolkit's
``scripts/check_review_labels.py``.

- A finding thread is one the bot opened with a devloop reviewer's marker, or a review
  app's own finding; findings and labels are read with the feedback loop's own rules.
- Every finding thread must be labelled, resolved or not: resolving a thread without a
  label leaves the status failing.
- Until every review in ``labels.wait_for`` and ``labels.wait_for_checks`` has finished on
  the head, a run with nothing unlabelled is ``pending``, never a pass. A review whose
  status is a failure or an error, or whose check did not conclude, did not finish either.
- A read that fails posts a failure: the check never passes on threads it could not read.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from devloop.review import feedback as fb
from devloop.review import status_guard as guard
from devloop.review.github import GITHUB_API, Http, http

#: The commit status this check posts.
CONTEXT = "Review labels"
#: Review status states that mean the review did not finish: not judged, never a pass.
UNFINISHED_STATES = ("failure", "error")
#: Check-run conclusions that mean a review app finished; any other may mean missing findings.
FINISHED_CONCLUSIONS = ("success", "neutral", "skipped")


@dataclass(frozen=True)
class FindingThread:
    """One finding's thread: who raised it, where, and whether it is labelled."""

    reviewer: str
    path: str
    url: str
    resolved: bool
    labelled: bool


def findings(
    threads: Sequence[Mapping[str, Any]], reviewers: Mapping[str, str], prefixes: Sequence[str]
) -> list[FindingThread]:
    """Return every finding thread in ``threads``, in order."""
    found = []
    for thread in threads:
        what = fb.finding(thread, reviewers)
        if what is not None:
            found.append(
                FindingThread(
                    reviewer=what["reviewer"],
                    path=what["path"],
                    url=what["url"],
                    resolved=bool(thread.get("isResolved")),
                    labelled=bool(fb.labels_of(thread, prefixes)),
                )
            )
    return found


def verdict(found: Sequence[FindingThread], prefixes: Sequence[str]) -> tuple[str, str, list[str]]:
    """Return the status state, its description and a log line per unlabelled thread.

    The description is built from counts only, so no model or path text reaches it.
    """
    words = " or ".join(f"{p}:" for p in prefixes)
    missing = [thread for thread in found if not thread.labelled]
    lines = [
        f"- {t.reviewer} finding on {t.path or '(no path)'} "
        f"({'resolved' if t.resolved else 'open'}): {t.url}"
        for t in missing
    ]
    if not found:
        return "success", "No review findings on this pull request", lines
    if not missing:
        return "success", f"All {len(found)} finding thread(s) have a {words} reply", lines
    return (
        "failure",
        f"{len(missing)} of {len(found)} finding thread(s) need a {words} reply",
        lines,
    )


def waiting_for(
    statuses: Sequence[Mapping[str, Any]],
    checks: Sequence[Mapping[str, Any]],
    wait_for: Sequence[str],
    wait_for_checks: Sequence[str],
) -> list[str]:
    """Return why the reviews' findings are not all in yet, one reason per review.

    ``statuses`` holds each context's newest state (the combined status). A review with
    no status yet or still pending has not finished; a failure or an error did not
    finish. A check run that is missing, not completed, or concluded otherwise than in
    :data:`FINISHED_CONCLUSIONS` has not finished either.
    """
    current: dict[str, str] = {}
    for status in statuses:
        current.setdefault(str(status.get("context")), str(status.get("state")))
    reasons: list[str] = []
    for context in wait_for:
        state = current.get(context, "pending")
        if state == "pending":
            reasons.append(f"Waiting for {context}")
        elif state in UNFINISHED_STATES:
            reasons.append(f"{context} did not finish")
    for name in wait_for_checks:
        runs = [c for c in checks if c.get("name") == name]
        if not runs or any(c.get("status") != "completed" for c in runs):
            reasons.append(f"Waiting for {name}")
        elif any(c.get("conclusion") not in FINISHED_CONCLUSIONS for c in runs):
            reasons.append(f"{name} did not finish")
    return reasons


def write_outputs(path: str, **values: str) -> None:
    """Append ``values`` to ``$GITHUB_OUTPUT`` at ``path``, if there is one.

    The workflow reads ``sha`` (the head marked pending) and ``posted`` (a status is
    posted: the verdict, or the failure of a read that failed) to tell a run that
    reported from one that broke before it could.
    """
    if path:
        with open(path, "a", encoding="utf-8") as out:
            out.writelines(f"{key}={value}\n" for key, value in values.items())


@dataclass(frozen=True)
class Github:
    """The GitHub calls the check makes, each retried like the feedback loop's."""

    token: str
    repo: str
    send: Http = http
    sleep: Callable[[float], None] = time.sleep

    def get(self, url: str) -> dict[str, Any]:
        """Return the JSON object of a GET.

        Raises:
            FeedbackError: GitHub refused it, or answered with something else.

        """
        data = fb.request("GET", url, self.token, send=self.send, sleep=self.sleep).data
        if not isinstance(data, dict):
            raise fb.FeedbackError(f"GitHub's reply to GET {url.split('?')[0]} is not an object")
        return data

    def head(self, number: int) -> guard.Head:
        """Return the pull request's current head commit and whether its status is due.

        Raises:
            FeedbackError: GitHub could not be read, so the status could not be checked.

        """
        return guard.read_head(self.repo, number, self.token, send=self.send, sleep=self.sleep)

    def reviews(
        self, sha: str, wait_for_checks: Sequence[str]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Return the head's newest status per context, and its check runs by name."""
        base = f"{GITHUB_API}/repos/{self.repo}/commits/{sha}"
        statuses = list(self.get(f"{base}/status?per_page=100").get("statuses", []))
        checks: list[dict[str, Any]] = []
        for name in wait_for_checks:
            url = f"{base}/check-runs?check_name={quote(name, safe='')}&filter=latest"
            checks += list(self.get(url).get("check_runs", []))
        return statuses, checks

    def threads(self, number: int) -> list[dict[str, Any]]:
        """Return the pull request's review threads with all their comments."""
        owner, name = self.repo.split("/", 1)
        return fb.pr_threads(self.token, owner, name, number, send=self.send, sleep=self.sleep)

    def post(self, sha: str, state: str, description: str, url: str) -> None:
        """Post the ``Review labels`` status on ``sha``."""
        body = {"state": state, "context": CONTEXT, "description": description[:140]}
        fb.request(
            "POST",
            f"{GITHUB_API}/repos/{self.repo}/statuses/{sha}",
            self.token,
            {**body, "target_url": url},
            send=self.send,
            sleep=self.sleep,
        )


def run(
    gh: Github,
    number: int,
    run_url: str,
    reviewers: Mapping[str, str],
    prefixes: Sequence[str],
    wait_for: Sequence[str] = (),
    wait_for_checks: Sequence[str] = (),
    *,
    event_sha: str = "",
    outputs: str = "",
) -> int:
    """Check pull request ``number`` and post the status on its head; 0 once it is posted.

    The verdict is the commit status, not the exit code: a run that finds an unlabelled
    thread posts ``failure`` and still exits 0, because the check did its job. Only a run
    that could not check (a failed read) is a failed job. So a red job always means "look
    at this", and the status alone says whether the threads are labelled.

    The status goes to pending before the threads are read, so an earlier success never
    stands while this run checks. A failed read posts a failure and re-raises. When the
    pull request itself cannot be read, the failure goes on ``event_sha``, if any.

    A fork's pull request whose head is also the head of a branch in this repository gets
    no status at all, pending or verdict (:mod:`devloop.review.status_guard`): the run
    says so and returns 0. The head is read once, and the commit judged is the one posted on.

    Several runs can check one pull request at once (each reply starts one). After posting,
    the run reads again and posts once more if the answer has changed, so the run that
    finishes last leaves the status that matches the threads as they then are.

    Raises:
        FeedbackError: GitHub could not be read; a failure status was posted.

    """
    unreadable = "Could not read the review threads; re-run the check"
    try:
        pull = gh.head(number)
    except fb.FeedbackError:
        if event_sha:
            gh.post(event_sha, "failure", unreadable, run_url)
            write_outputs(outputs, sha=event_sha, posted="true")
        raise
    if not pull.due:
        print(guard.notice(CONTEXT, number, pull.sha))
        return 0
    head = pull.sha
    gh.post(head, "pending", "Checking the review threads for labels", run_url)
    write_outputs(outputs, sha=head)

    def read() -> tuple[str, str, list[str]]:
        """Return the verdict for the threads and reviews as they are now."""
        waiting = waiting_for(*gh.reviews(head, wait_for_checks), wait_for, wait_for_checks)
        state, description, lines = verdict(
            findings(gh.threads(number), reviewers, prefixes), prefixes
        )
        if waiting and state == "success":
            state, description = "pending", "; ".join(waiting)
        return state, description, lines

    try:
        state, description, lines = read()
        gh.post(head, state, description, run_url)
        # Read once more: a reply may have landed, or a review finished, while this run
        # was posting. Every overlapping run does the same, so the last to finish posts
        # the answer for the threads as they are at its end.
        again = read()
        if again[:2] != (state, description):
            state, description, lines = again
            gh.post(head, state, description, run_url)
    except fb.FeedbackError:
        gh.post(head, "failure", unreadable, run_url)
        write_outputs(outputs, posted="true")
        raise
    write_outputs(outputs, posted="true")
    print(f"{CONTEXT}: {state}: {description}")
    for line in lines:
        print(line)
    return 0


def main(root: Path, env: Mapping[str, str]) -> int:
    """Run the check from the workflow's environment; 2 when it cannot start."""
    from devloop.config import ConfigError, load_config
    from devloop.review.command import marker_names

    token, repo = env.get("GITHUB_TOKEN", ""), env.get("GITHUB_REPOSITORY", "")
    number = env.get("PR_NUMBER", "").strip()
    try:
        config = load_config(root / "devloop.toml")
    except ConfigError as error:
        print(f"devloop labels: {error}", file=sys.stderr)
        return 2
    if not token or "/" not in repo or not number.isdigit():
        print(
            "devloop labels: GITHUB_TOKEN, GITHUB_REPOSITORY (owner/name) and a numeric "
            "PR_NUMBER are required",
            file=sys.stderr,
        )
        return 2
    labels = config.labels
    try:
        return run(
            Github(token, repo),
            int(number),
            env.get("RUN_URL", ""),
            marker_names(config),
            labels.prefixes,
            labels.wait_for,
            labels.wait_for_checks,
            event_sha=env.get("HEAD_SHA", "").strip(),
            outputs=env.get("GITHUB_OUTPUT", ""),
        )
    except fb.FeedbackError as error:
        print(f"devloop labels: {error}", file=sys.stderr)
        return 1

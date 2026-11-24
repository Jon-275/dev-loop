"""Post the ``Council review`` commit status on a pull request's head.

Two writers keep it current (decision 0003: not judged is never a pass):

- **CI** (``council.yml``, on every push) posts without a report: pending while the
  head's tier needs a council, or success where none is required. It never overwrites a
  success or failure already on that exact head, which only a report can have posted.
- **A maintainer** posts a council report's verdict (``--report``) after the
  ``council-review`` skill ran, whatever stands on the head.

The head and the changed files are read from GitHub, never from a local checkout, so the
verdict is for the pull request as GitHub will merge it. A read that fails posts nothing.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from devloop.config import Config
from devloop.council import RoleCard, plan
from devloop.council_report import (
    NOT_REQUIRED,
    Report,
    ReportError,
    Verdict,
    judge,
    parse_report,
)
from devloop.review import feedback as fb
from devloop.review.github import GITHUB_API, Http, Reply, http
from devloop.review.run import pr_tier

CONTEXT = "Council review"
#: GitHub lists at most 3000 files of a pull request; a longer list is cut short.
MAX_FILES = 3000
#: The states only a report posts; CI leaves them in place on the same head.
VERDICT_STATES = ("success", "failure")


@dataclass
class Api:
    """The GitHub calls the status needs, each retried like the feedback loop's."""

    token: str
    repo: str
    send: Http = http
    sleep: Callable[[float], None] = time.sleep

    def _get(self, url: str) -> Reply:
        """Return one GET's reply: its JSON and the next page's URL."""
        return fb.request("GET", url, self.token, send=self.send, sleep=self.sleep)

    def head(self, number: int) -> str:
        """Return the pull request's current head commit."""
        data = self._get(f"{GITHUB_API}/repos/{self.repo}/pulls/{number}").data
        if not isinstance(data, dict) or not isinstance(data.get("head"), dict):
            raise fb.FeedbackError(f"GitHub's reply for pull request {number} has no head")
        return str(data["head"]["sha"])

    def files(self, number: int) -> list[str]:
        """Return every file the pull request changes, over every page."""
        url: str | None = f"{GITHUB_API}/repos/{self.repo}/pulls/{number}/files?per_page=100"
        found: list[str] = []
        listed = 0  # GitHub's limit counts its entries, not the paths a rename adds
        while url:
            reply = self._get(url)
            if not isinstance(reply.data, list):
                raise fb.FeedbackError("GitHub's list of the pull request's files is not a list")
            listed += len(reply.data)
            for item in reply.data:
                # A rename counts where it came from too, so moving code never lowers a tier.
                found.append(str(item["filename"]))
                if item.get("previous_filename"):
                    found.append(str(item["previous_filename"]))
            url = reply.next_url
        if listed >= MAX_FILES:
            raise fb.FeedbackError(f"the pull request lists {listed} files, GitHub's limit")
        return found

    def current(self, sha: str) -> Verdict | None:
        """Return the head's newest Council review status, or None."""
        data = self._get(f"{GITHUB_API}/repos/{self.repo}/commits/{sha}/status?per_page=100").data
        statuses = data.get("statuses", []) if isinstance(data, dict) else []
        found = next((s for s in statuses if s.get("context") == CONTEXT), None)
        return Verdict(str(found["state"]), str(found.get("description") or "")) if found else None

    def post(self, sha: str, verdict: Verdict, url: str) -> None:
        """Post ``verdict`` as the Council review status on ``sha``."""
        body = {"state": verdict.state, "context": CONTEXT, "description": verdict.description}
        if url:
            body["target_url"] = url
        fb.request(
            "POST",
            f"{GITHUB_API}/repos/{self.repo}/statuses/{sha}",
            self.token,
            body,
            send=self.send,
            sleep=self.sleep,
        )


def publish(
    api: Api,
    number: int,
    cards: Mapping[str, RoleCard],
    config: Config,
    report: Report | None,
    run_url: str = "",
) -> Verdict:
    """Judge pull request ``number``'s head and post the Council review status on it.

    With no ``report`` (CI), a council's success or failure already on the head is kept
    and nothing is posted; CI's own "not required" success is judged again. A verdict
    posted while CI runs can be overwritten with pending (never with a pass): post it
    again. Raises FeedbackError when GitHub cannot be read; then nothing is posted.
    """
    head = api.head(number)
    paths = api.files(number)
    chosen = plan(cards, paths, pr_tier(paths, config.tiers))
    verdict = judge(report, head, chosen, config.council.require_status, pr=number)
    if report is None and verdict.state == "pending":
        standing = api.current(head)
        if (
            standing
            and standing.state in VERDICT_STATES
            and NOT_REQUIRED not in standing.description
        ):
            return Verdict(standing.state, "kept the council's verdict already on this head")
    api.post(head, verdict, run_url)
    return verdict


def _gh(*args: str) -> str:
    """Return the GitHub CLI's output for ``args``, or an empty string."""
    try:
        done = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    except OSError:
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def cli(args: argparse.Namespace, cards: Mapping[str, RoleCard], config: Config) -> int:
    """Run ``devloop council report --post``: 0 success, 1 pending or failure, 2 no post.

    2 covers every run that posted nothing: missing settings, an unreadable report, or a
    GitHub read that failed.
    """
    if args.pr is None:
        print("devloop council report: --post needs --pr", file=sys.stderr)
        return 2
    token = os.environ.get("GITHUB_TOKEN") or _gh("auth", "token")
    repo = (
        args.repo
        or os.environ.get("GITHUB_REPOSITORY")
        or _gh("repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner")
    )
    if not token or "/" not in repo:
        print(
            "devloop council report: needs GITHUB_TOKEN (or gh signed in) and the "
            "repository (--repo owner/name)",
            file=sys.stderr,
        )
        return 2
    try:
        report = parse_report(args.report.read_text(encoding="utf-8")) if args.report else None
        verdict = publish(Api(token, repo), args.pr, cards, config, report, args.run_url)
    except (ReportError, OSError, UnicodeDecodeError) as error:
        print(f"devloop council report: {error}", file=sys.stderr)
        return 2
    except fb.FeedbackError as error:  # a failed read fails the run, so it is seen
        print(f"devloop council report: could not read GitHub; nothing posted: {error}")
        return 2
    print(f"Council review on #{args.pr}: {verdict.state}: {verdict.description}")
    return 0 if verdict.state == "success" else 1

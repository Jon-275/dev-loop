"""``devloop main-health``: open, update or close one workflow's "main is red" issue.

Run on every push to ``main``, after the workflow's other jobs, whatever they did.
``--needs`` is ``toJSON(needs)``: the result of every job the workflow ran. A job that
failed or was cancelled (GitHub reports a job that hit its timeout as ``cancelled``)
makes main red for that workflow:

- red: one open issue per workflow, titled ``main is red: <workflow>`` with the
  ``main-red`` label, is created, or commented on when already open;
- green: that issue, if open, is closed with a comment naming the commit.

One issue per workflow, so one workflow going green never closes another's issue. Only
the run for main's current head reports, because runs can finish out of order. It needs
the ``gh`` CLI with ``GH_TOKEN`` able to write issues.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping
from typing import Any

LABEL = "main-red"
RED_RESULTS = frozenset({"failure", "cancelled"})

Gh = Callable[[list[str]], str]


def red_jobs(needs: Mapping[str, Any]) -> list[str]:
    """Return the jobs whose result makes main red, in name order."""
    return sorted(name for name, job in needs.items() if job.get("result") in RED_RESULTS)


def title(workflow: str) -> str:
    """Return the issue title for ``workflow``."""
    return f"main is red: {workflow}"


def run_gh(args: list[str]) -> str:
    """Run ``gh`` and return its output."""
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def open_issue(gh: Gh, workflow: str) -> int | None:
    """Return the number of this workflow's open main-red issue, if there is one."""
    found = json.loads(
        gh(
            ["issue", "list", "--state", "open", "--label", LABEL]
            + ["--limit", "100", "--json", "number,title"]
        )
    )
    for issue in found:
        if issue["title"] == title(workflow):
            return int(issue["number"])
    return None


def main_head(gh: Gh) -> str:
    """Return the commit main points at now."""
    return gh(["api", "repos/{owner}/{repo}/commits/main", "--jq", ".sha"]).strip()


def report(gh: Gh, workflow: str, sha: str, run_url: str, needs: Mapping[str, Any]) -> str:
    """Bring the issue in line with this run and return what was done."""
    head = main_head(gh)
    if head != sha:
        return f"skipped: main has moved on to {head[:12]}, whose run reports instead"
    red = red_jobs(needs)
    number = open_issue(gh, workflow)
    if red:
        detail = ", ".join(f"`{name}` {needs[name]['result']}" for name in red)
        body = f"{sha[:12]}: {detail}. Run: {run_url}"
        if number is not None:
            gh(["issue", "comment", str(number), "--body", f"Still red at {body}"])
            return "commented"
        gh(
            ["label", "create", LABEL, "--color", "B60205", "--force"]
            + ["--description", "a workflow is failing on main"]
        )
        gh(
            ["issue", "create", "--title", title(workflow), "--label", LABEL]
            + ["--body", f"{workflow} is failing on main.\n\n{body}"]
        )
        return "opened"
    if number is not None:
        gh(["issue", "close", str(number), "--comment", f"Green again at {sha[:12]}: {run_url}"])
        return "closed"
    return "nothing to do"

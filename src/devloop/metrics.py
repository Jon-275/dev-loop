"""``devloop metrics``: measure the review loop over the pull requests merged in a window.

Ported from gtm_toolkit's ``scripts/loop_metrics.py``. Per pull request: size, hours to
merge, commits, review app reviews, finding threads and their labels (read with the
feedback loop's own rules), and model tokens from the ``model-usage`` markers. Nothing is
dropped silently: truncated pull requests, a summary without a marker and a failed run
with no summary of its own are named, never counted as zero.
"""

from __future__ import annotations

import math
import re
import statistics
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from devloop.agent import agent_of
from devloop.review import feedback as fb

#: Another reviewer's thread this close on the same file is the same finding.
NEAR_LINES = 3
USAGE_RE = re.compile(
    r"<!-- model-usage: requests=(\d+) input=(\d+) output=(\d+) unreported=(\d+)"
    r"(?: failed=(\d+))?(?: run=(\d+))? -->"  # older markers lack ``failed`` (0) and ``run``
)
UNFINISHED_STATES = frozenset({"FAILURE", "ERROR", "PENDING"})
RUN_URL_RE = re.compile(r"/actions/runs/(\d+)")
SUMMARY_RE = re.compile(r"<!-- ([a-z][a-z0-9-]*)-review ")
_EMPTY = {"pageInfo": {"hasNextPage": False}, "nodes": []}


@dataclass(frozen=True)
class Names:
    """Who the report counts: devloop reviewers by marker and status, apps, label words."""

    #: Marker prefix (``gemini-finding``) to reviewer name.
    reviewers: Mapping[str, str]
    #: Commit status context (``Gemini review``) to reviewer name.
    statuses: Mapping[str, str]
    prefixes: Sequence[str] = ("accepted", "rejected")

    @property
    def all(self) -> list[str]:
        """Return every reviewer the report has a row for: review apps, then devloop's."""
        apps = sorted(set(fb.APP_NAMES.values()))
        return apps + sorted(set(self.reviewers.values()) - set(apps))


def size_buckets(max_lines: int) -> tuple[tuple[int, int | None], ...]:
    """Return the changed-line buckets of the size table around the project's limit."""
    return (
        (0, max_lines // 4),
        (max_lines // 4, max_lines),
        (max_lines, max_lines * 5 // 2),
        (max_lines * 5 // 2, max_lines * 25 // 4),
        (max_lines * 25 // 4, None),
    )


QUERY = """
query($search: String!, $after: String) {
  search(query: $search, type: ISSUE, first: 10, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on PullRequest {
        number title createdAt mergedAt additions deletions
        commits(last: 100) {
          totalCount
          pageInfo { hasPreviousPage }
          nodes { commit { oid message status { contexts { context state targetUrl } } } }
        }
        reviews(first: 50) {
          pageInfo { hasNextPage }
          nodes { author { login } body commit { oid } }
        }
        reviewThreads(first: 100) {
          pageInfo { hasNextPage }
          nodes {
            isResolved path line originalLine
            comments(first: 20) {
              pageInfo { hasNextPage }
              nodes { body authorAssociation author { login } }
            }
          }
        }
      }
    }
  }
}
"""


@dataclass(frozen=True)
class Thread:
    """One automated finding thread, as the feedback loop reads it."""

    reviewer: str
    severity: str
    path: str
    line: int | None
    outcome: str | None  # "accepted", "rejected" or None (unlabelled)


@dataclass
class PullRequest:
    """One merged pull request's loop figures."""

    number: int
    title: str
    created: datetime
    merged: datetime
    lines: int
    commits: int
    #: Reviews a review app posted (each one may be billed).
    app_reviews: int = 0
    threads: list[Thread] = field(default_factory=list)
    #: Per reviewer, from summaries that carry a usage marker: input tokens,
    #: output tokens, requests whose usage the provider did not report, and
    #: attempts that failed (their tokens, if billed, are not in the total).
    tokens: dict[str, list[int]] = field(default_factory=dict)
    #: Per reviewer, summaries without a usage marker (their tokens unmeasured).
    unmeasured_summaries: dict[str, int] = field(default_factory=dict)
    #: Per reviewer, commits whose latest status failed or never finished and
    #: that carry no summary from it: runs whose tokens were not measured.
    unmeasured_runs: dict[str, int] = field(default_factory=dict)
    #: Threads, comments or reviews beyond the first page (not counted).
    truncated: bool = False
    #: The tool that wrote it, from its commits' ``Agent:`` trailers: one tool, ``mixed``
    #: when they name several, ``unknown`` when none has one (a person, or no trailer).
    agent: str = "unknown"
    #: Review rounds: the commits a devloop reviewer posted a summary on.
    rounds: int = 0

    @property
    def hours(self) -> float:
        """Hours from open to merge."""
        return (self.merged - self.created).total_seconds() / 3600


def _time(text: str) -> datetime:
    """Return a GitHub timestamp as an aware datetime."""
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def thread_from(node: Mapping[str, Any], names: Names) -> Thread | None:
    """Return the finding in a review thread node, or ``None`` when no reviewer opened it."""
    thread = {**node, "comments": node["comments"]["nodes"]}
    found = fb.finding(thread, names.reviewers)
    if found is None:
        return None
    labels = fb.labels_of(thread, names.prefixes)
    line = node.get("line") or node.get("originalLine")
    return Thread(
        reviewer=found["reviewer"],
        severity=found["severity"],
        path=found["path"],
        line=int(line) if line else None,
        outcome=labels[-1][0] if labels else None,
    )


def pull_request_from(node: Mapping[str, Any], names: Names) -> PullRequest:
    """Return the loop figures of one pull request search result."""
    pr = PullRequest(
        number=int(node["number"]),
        title=str(node["title"]),
        created=_time(node["createdAt"]),
        merged=_time(node["mergedAt"]),
        lines=int(node["additions"]) + int(node["deletions"]),
        commits=int(node["commits"]["totalCount"]),
    )
    reviews, threads = node.get("reviews", _EMPTY), node.get("reviewThreads", _EMPTY)
    pr.truncated = bool(reviews["pageInfo"]["hasNextPage"] or threads["pageInfo"]["hasNextPage"])
    pr.truncated |= bool(node["commits"].get("pageInfo", {}).get("hasPreviousPage"))
    #: Per (reviewer, commit), the run ids its summaries name ("" for a summary naming none).
    summarised: dict[tuple[str, str], set[str]] = {}
    for review in reviews["nodes"]:
        login = (review.get("author") or {}).get("login")
        body = str(review.get("body") or "")
        if login in fb.APP_NAMES:
            pr.app_reviews += 1
        elif login in fb.BOT_LOGINS and (summary := SUMMARY_RE.search(body)):
            reviewer = names.reviewers.get(f"{summary.group(1)}-finding")
            if reviewer is None:
                continue
            key = (reviewer, str((review.get("commit") or {}).get("oid")))
            usage = USAGE_RE.search(body)
            summarised.setdefault(key, set()).add((usage and usage.group(6)) or "")
            if usage is None:
                pr.unmeasured_summaries[reviewer] = pr.unmeasured_summaries.get(reviewer, 0) + 1
            else:
                _, given, made, unreported, failed = (int(n or 0) for n in usage.groups()[:5])
                totals = pr.tokens.setdefault(reviewer, [0, 0, 0, 0])
                for index, value in enumerate((given, made, unreported, failed)):
                    totals[index] += value
    commits = [c["commit"] for c in node["commits"].get("nodes", [])]
    tools = {tool for c in commits if (tool := agent_of(str(c.get("message") or "")))}
    pr.agent = tools.pop() if len(tools) == 1 else "mixed" if tools else "unknown"
    pr.rounds = len({oid for _, oid in summarised})
    counted: set[tuple[str, str]] = set()  # one latest status per reviewer and commit
    for commit in commits:
        for status in (commit.get("status") or {}).get("contexts", []):
            name = names.statuses.get(str(status.get("context")))
            oid = str(commit.get("oid"))
            if not name or (name, oid) in counted or status.get("state") not in UNFINISHED_STATES:
                continue
            counted.add((name, oid))
            runs = summarised.get((name, oid), set())
            run = RUN_URL_RE.search(str(status.get("targetUrl") or ""))
            # A status that names its run is measured only by a summary naming that run: a
            # summary with no run id may be another run's, so it never covers a named one.
            # A status naming no run can only be matched by its commit.
            measured = run.group(1) in runs if run else bool(runs)
            if not measured:
                pr.unmeasured_runs[name] = pr.unmeasured_runs.get(name, 0) + 1
    for node_thread in threads["nodes"]:
        pr.truncated |= bool(node_thread["comments"]["pageInfo"]["hasNextPage"])
        if (found := thread_from(node_thread, names)) is not None:
            pr.threads.append(found)
    return pr


#: Sends one GraphQL query: ``(query, variables) -> data``.
Query = Callable[[str, Mapping[str, Any]], dict[str, Any]]


def merged_pull_requests(
    query: Query, repo: str, since: datetime, until: datetime, names: Names
) -> list[PullRequest]:
    """Every pull request merged in ``[since, until)``, oldest merge first.

    The search uses UTC timestamps, and every result is filtered again on its
    own ``mergedAt``.
    """
    stamp = "%Y-%m-%dT%H:%M:%SZ"
    search = (
        f"repo:{repo} is:pr is:merged "
        f"merged:{since.astimezone(UTC):{stamp}}..{until.astimezone(UTC):{stamp}}"
    )
    found: dict[int, PullRequest] = {}
    after: str | None = None
    while True:
        page = query(QUERY, {"search": search, "after": after})
        page = page["search"]
        for node in page["nodes"]:
            if node and node.get("mergedAt"):
                pr = pull_request_from(node, names)
                if since <= pr.merged < until:
                    found[pr.number] = pr
        if not page["pageInfo"]["hasNextPage"]:
            return sorted(found.values(), key=lambda pr: pr.merged)
        after = page["pageInfo"]["endCursor"]


def unique_accepted(threads: Sequence[Thread]) -> Counter[str]:
    """Return accepted findings per reviewer that no other reviewer raised near the same line."""
    counts: Counter[str] = Counter()
    for thread in threads:
        if thread.outcome != "accepted":
            continue
        shared = any(
            other.reviewer != thread.reviewer
            and other.path == thread.path
            and (
                other.line is None
                or thread.line is None
                or abs(other.line - thread.line) <= NEAR_LINES
            )
            for other in threads
        )
        if not shared:
            counts[thread.reviewer] += 1
    return counts


# --- the report ---------------------------------------------------------------------


def _median(values: Iterable[float]) -> float | None:
    """Return the median, or ``None`` (not measured) for no values."""
    items = list(values)
    return float(statistics.median(items)) if items else None


def _p75(values: Iterable[float]) -> float | None:
    """Return the 75th percentile by nearest rank (the ``ceil(0.75 n)``-th), ``None`` if none."""
    items = sorted(values)
    return float(items[math.ceil(0.75 * len(items)) - 1]) if items else None


def _round(value: float | None) -> float | None:
    """``value`` to one decimal, keeping ``None``."""
    return None if value is None else round(value, 1)


def summarise(prs: Sequence[PullRequest], names: Names, max_lines: int = 800) -> dict[str, Any]:
    """Return the report's figures as JSON-ready data."""
    threads = [t for pr in prs for t in pr.threads]
    unique = Counter[str]()
    for pr in prs:
        unique.update(unique_accepted(pr.threads))
    reviewers: dict[str, dict[str, Any]] = {}
    for name in names.all:
        mine = [t for t in threads if t.reviewer == name]
        accepted = sum(t.outcome == "accepted" for t in mine)
        rejected = sum(t.outcome == "rejected" for t in mine)
        tokens = [pr.tokens[name] for pr in prs if name in pr.tokens]
        reviewers[name] = {
            "threads": len(mine),
            "by_severity": dict(sorted(Counter(t.severity for t in mine).items())),
            "accepted": accepted,
            "rejected": rejected,
            "unlabelled": len(mine) - accepted - rejected,
            "precision": round(accepted / (accepted + rejected), 3)
            if accepted + rejected
            else None,
            "unique_accepted": unique[name],
            "input_tokens": sum(t[0] for t in tokens) if tokens else None,
            "output_tokens": sum(t[1] for t in tokens) if tokens else None,
            "unreported_requests": sum(t[2] for t in tokens),
            "failed_attempts": sum(t[3] for t in tokens),
            "unmeasured_summaries": sum(pr.unmeasured_summaries.get(name, 0) for pr in prs),
            "unmeasured_runs": sum(pr.unmeasured_runs.get(name, 0) for pr in prs),
        }
    sizes = []
    for low, high in size_buckets(max_lines):
        group = [pr for pr in prs if pr.lines >= low and (high is None or pr.lines < high)]
        sizes.append(
            {
                "lines": f"{low}+" if high is None else f"{low}-{high - 1}",
                "prs": len(group),
                "median_hours": _round(_median(pr.hours for pr in group)),
                "median_threads": _median(len(pr.threads) for pr in group),
                "median_commits": _median(pr.commits for pr in group),
            }
        )
    labelled = sum(t.outcome is not None for t in threads)
    by_tool = {}
    for tool in sorted({pr.agent for pr in prs}):
        group = [pr for pr in prs if pr.agent == tool]
        mine = [t for pr in group for t in pr.threads]
        judged = [t for t in mine if t.outcome in ("accepted", "rejected")]
        by_tool[tool] = {
            "prs": len(group),
            "median_hours": _round(_median(pr.hours for pr in group)),
            "median_threads": _median(len(pr.threads) for pr in group),
            "median_rounds": _median(pr.rounds for pr in group),
            "rejected_share": round(sum(t.outcome == "rejected" for t in judged) / len(judged), 3)
            if judged
            else None,
            "median_lines": _median(pr.lines for pr in group),
            "over_max_lines": sum(pr.lines > max_lines for pr in group),
        }
    return {
        "prs": len(prs),
        "median_hours": _round(_median(pr.hours for pr in prs)),
        "p75_hours": _round(_p75(pr.hours for pr in prs)),
        "median_threads": _median(len(pr.threads) for pr in prs),
        "mean_threads": round(len(threads) / len(prs), 1) if prs else None,
        "threads": len(threads),
        "labelled_share": round(labelled / len(threads), 3) if threads else None,
        "max_lines": max_lines,
        "over_max_lines": [pr.number for pr in prs if pr.lines > max_lines],
        "app_reviews": sum(pr.app_reviews for pr in prs),
        "reviewers": reviewers,
        "by_tool": by_tool,
        "sizes": sizes,
        "not_counted": {
            "truncated_prs": [pr.number for pr in prs if pr.truncated],
            "escaped_defects": "not measured",
            "ci_minutes": "not measured",
        },
    }


#: The per-reviewer counts of tokens left out of the totals.
_LEFT_OUT = ("unreported_requests", "failed_attempts", "unmeasured_summaries", "unmeasured_runs")


def render(data: Mapping[str, Any], since: datetime, until: datetime) -> str:
    """Return the report as Markdown, every figure and every exclusion named."""

    def num(value: object) -> str:
        """Return a figure, or "n/a" when it could not be measured."""
        return "n/a" if value is None else f"{value:,}" if isinstance(value, int) else str(value)

    lines = [
        f"# Review loop metrics: {since:%Y-%m-%d} to {until:%Y-%m-%d}",
        "",
        f"- Merged pull requests: {data['prs']}",
        f"- Open to merge: median {num(data['median_hours'])} h, p75 {num(data['p75_hours'])} h",
        f"- Finding threads: {data['threads']} (median {num(data['median_threads'])}, "
        f"mean {num(data['mean_threads'])} per PR); labelled share {num(data['labelled_share'])}",
        f"- Over {data['max_lines']} changed lines: {len(data['over_max_lines'])} "
        + (
            f"({', '.join(f'#{n}' for n in data['over_max_lines'])})"
            if data["over_max_lines"]
            else ""
        ),
        f"- Review app reviews (each may be billed): {data['app_reviews']}",
        "",
        "## By reviewer",
        "",
        "| Reviewer | Threads | By severity | Accepted | Rejected | Unlabelled | Precision "
        "| Unique accepted | Input tokens | Output tokens |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, r in data["reviewers"].items():
        severity = ", ".join(f"{k} {v}" for k, v in r["by_severity"].items()) or "-"
        low = " (low)" if any(r[k] for k in _LEFT_OUT) else ""  # some tokens not counted
        lines.append(
            f"| {name} | {r['threads']} | {severity} | {r['accepted']} | {r['rejected']} "
            f"| {r['unlabelled']} | {num(r['precision'])} | {r['unique_accepted']} "
            f"| {num(r['input_tokens'])}{low} | {num(r['output_tokens'])}{low} |"
        )
    lines += [
        "",
        "## By authoring tool",
        "",
        "From each pull request's `Agent:` commit trailers: `mixed` when its commits name "
        "several tools, `unknown` when none has one. Rejected share is rejected of labelled "
        "findings.",
        "",
        "| Tool | PRs | Median hours | Median threads | Median rounds | Rejected share "
        f"| Median lines | Over {data['max_lines']} |",
        "|---|---|---|---|---|---|---|---|",
        *(
            f"| {tool} | {t['prs']} | {num(t['median_hours'])} | {num(t['median_threads'])} "
            f"| {num(t['median_rounds'])} | {num(t['rejected_share'])} "
            f"| {num(t['median_lines'])} | {t['over_max_lines']} |"
            for tool, t in data["by_tool"].items()
        ),
    ]
    lines += [
        "",
        "## By size",
        "",
        "| Changed lines | PRs | Median hours | Median threads | Median commits |",
        "|---|---|---|---|---|",
        *(
            f"| {s['lines']} | {s['prs']} | {num(s['median_hours'])} "
            f"| {num(s['median_threads'])} | {num(s['median_commits'])} |"
            for s in data["sizes"]
        ),
        "",
        "## Not counted",
        "",
    ]
    missing = data["not_counted"]
    lines += [
        f"- Pull requests with more than one page of reviews, threads or comments (only the "
        f"first page counted): {', '.join(f'#{n}' for n in missing['truncated_prs']) or 'none'}",
        "- Model tokens left out of the totals above: "
        + "; ".join(
            f"{name} {r['unmeasured_summaries']} summary(ies) without a token marker, "
            f"{r['unreported_requests']} request(s) with no reported usage, "
            f"{r['failed_attempts']} failed attempt(s), {r['unmeasured_runs']} failed or "
            "unfinished review run(s) with no summary from that run (latest status per commit only)"
            for name, r in data["reviewers"].items()
            if name not in fb.APP_NAMES.values()
        ),
        "- CI minutes: not measured.",
        "- Escaped defects: not measured.",
    ]
    return "\n".join(lines) + "\n"


def main(root: Path, days: int, json_path: Path | None, env: Mapping[str, str]) -> int:
    """Print the report for the last ``days`` days (and write ``json_path``); 2 on bad setup."""
    import json
    import sys
    from datetime import timedelta

    from devloop.config import ConfigError, load_config
    from devloop.review.command import marker_names

    token, repo = env.get("GITHUB_TOKEN", ""), env.get("GITHUB_REPOSITORY", "")
    try:
        config = load_config(root / "devloop.toml")
    except ConfigError as error:
        print(f"devloop metrics: {error}", file=sys.stderr)
        return 2
    if not token or "/" not in repo or days < 1:
        print("devloop metrics: GITHUB_TOKEN, GITHUB_REPOSITORY and --days >= 1", file=sys.stderr)
        return 2
    reviewers = marker_names(config)
    statuses = {f"{title} review": title for title in reviewers.values()}
    names = Names(reviewers, statuses, config.labels.prefixes)
    until = datetime.now(UTC)
    since = until - timedelta(days=days)

    def query(text: str, variables: Mapping[str, Any]) -> dict[str, Any]:
        """Send one GraphQL query with the feedback loop's retries."""
        return fb.graphql(token, text, variables, sleep=time.sleep)

    try:
        prs = merged_pull_requests(query, repo, since, until, names)
    except fb.FeedbackError as error:
        print(f"devloop metrics: {error}", file=sys.stderr)
        return 1
    data = summarise(prs, names, config.pr.max_lines)
    data["since"], data["until"] = since.isoformat(), until.isoformat()
    print(render(data, since, until), end="")
    if json_path is not None:
        json_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return 0

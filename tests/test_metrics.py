"""``devloop metrics``: the review loop's figures, read with the feedback loop's rules."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from review_helpers import PROVIDER

from devloop import metrics
from devloop.review.model import DiffLines, Finding
from devloop.review.threads import anchor

GEMINI = replace(
    PROVIDER, name="Gemini", marker_prefix="gemini-finding", summary_marker="gemini-review"
)
NAMES = metrics.Names(
    {"gemini-finding": "Gemini", "reflection-finding": "Reflection"},
    {"Gemini review": "Gemini", "Reflection review": "Reflection"},
)
T0 = datetime(2026, 11, 16, tzinfo=UTC)


def _from(node: dict[str, Any]) -> metrics.PullRequest:
    """Return one pull request's figures, with the test's reviewer names."""
    return metrics.pull_request_from(node, NAMES)


def _comments(*nodes: dict[str, Any], more: bool = False) -> dict[str, Any]:
    """Return a thread's ``comments`` connection."""
    return {"pageInfo": {"hasNextPage": more}, "nodes": list(nodes)}


def _gemini(line: int = 10, path: str = "a.py") -> dict[str, Any]:
    """Return the first comment of a Gemini finding thread, as the bot posts it."""
    finding = Finding("P2", path, line, "Stale field", "b", None)
    comment = anchor(GEMINI, finding, {path: DiffLines(right={line})})
    assert comment is not None
    body = comment["body"]
    return {"body": body, "authorAssociation": "NONE", "author": {"login": "github-actions"}}


def _greptile(severity: str = "P1") -> dict[str, Any]:
    """Return the first comment of a Greptile finding thread."""
    body = f'<img alt="{severity}" src="x"> **Lease not renewed**\n\nDetails.'
    return {"body": body, "authorAssociation": "NONE", "author": {"login": "greptile-apps"}}


def _reply(text: str, association: str = "OWNER") -> dict[str, Any]:
    """Return a reply by a repository owner unless said otherwise."""
    return {"body": text, "authorAssociation": association, "author": {"login": "jon"}}


def _thread(*comments: dict[str, Any], path: str = "a.py", line: int = 10) -> dict[str, Any]:
    """Return a review thread node."""
    return {
        "isResolved": True,
        "path": path,
        "line": line,
        "originalLine": line,
        "comments": _comments(*comments),
    }


def _node(number: int, *threads: dict[str, Any], **extra: object) -> dict[str, Any]:
    """Return a pull request search result."""
    node: dict[str, Any] = {
        "number": number,
        "title": extra.get("title", f"feat: change {number}"),
        "createdAt": extra.get("created", "2026-11-17T10:00:00Z"),
        "mergedAt": extra.get("merged", "2026-11-17T12:00:00Z"),
        "additions": extra.get("additions", 100),
        "deletions": 0,
        "commits": {"totalCount": 3, "nodes": extra.get("commits", [])},
        "reviews": {"pageInfo": {"hasNextPage": False}, "nodes": extra.get("reviews", [])},
        "reviewThreads": {
            "pageInfo": {"hasNextPage": extra.get("more_threads", False)},
            "nodes": list(threads),
        },
    }
    return node


# --- reading one pull request ------------------------------------------------------


def test_findings_and_labels_follow_the_feedback_loop_s_rules() -> None:
    """A member's ``accepted:`` labels; a non-member's does not; a person's thread is ignored."""
    pr = _from(
        _node(
            1,
            _thread(_gemini(), _reply("accepted: fixed")),
            _thread(_greptile(), _reply("accepted: fixed", association="NONE"), line=40),
            _thread(_reply("Why this name?"), line=70),
        )
    )
    assert [(t.reviewer, t.severity, t.outcome) for t in pr.threads] == [
        ("Gemini", "P2", "accepted"),
        ("Greptile", "P1", None),
    ]
    assert pr.hours == 2.0 and pr.lines == 100


def test_token_markers_are_summed_and_a_missing_one_is_unmeasured() -> None:
    """Usage comes from the summary marker; a summary without one is counted, never zero."""
    marker = "<!-- model-usage: requests=1 input=1000 output=200 unreported=0 -->"
    with_marker = f"<!-- gemini-review findings=0 -->\n{marker}"
    reviews = [
        {"author": {"login": "github-actions"}, "body": with_marker},
        {"author": {"login": "github-actions"}, "body": "<!-- reflection-review findings=0 -->"},
        {"author": {"login": "greptile-apps"}, "body": "Summary"},
        {"author": {"login": "greptile-apps"}, "body": "Summary"},
    ]
    pr = metrics.pull_request_from(_node(2, reviews=reviews), NAMES)
    assert pr.tokens == {"Gemini": [1000, 200, 0, 0]}  # an old marker: no failed field
    assert pr.unmeasured_summaries == {"Reflection": 1} and pr.app_reviews == 2


def _commit(oid: str, run: str = "", **states: str) -> dict[str, Any]:
    """Return a commit whose statuses, set by run ``run``, are ``Name=state`` ("Gemini review")."""
    url = f"https://github.com/o/r/actions/runs/{run}" if run else None
    contexts = [
        {"context": f"{name} review", "state": state, "targetUrl": url}
        for name, state in states.items()
    ]
    return {"commit": {"oid": oid, "status": {"contexts": contexts}}}


def _summary(oid: str, run: str = "") -> dict[str, Any]:
    """Return a Gemini summary on commit ``oid`` whose usage marker names run ``run``."""
    named = f" run={run}" if run else ""
    marker = f"<!-- model-usage: requests=1 input=500 output=50 unreported=0 failed=0{named} -->"
    body = f"<!-- gemini-review findings=0 -->\n{marker}"
    return {"author": {"login": "github-actions"}, "body": body, "commit": {"oid": oid}}


@pytest.mark.parametrize(
    ("summary_run", "status_run", "unmeasured"),
    [
        ("101", "102", {"Gemini": 1}),  # a failed re-run after an earlier run's summary
        ("101", "101", {}),  # a partial review: its summary, then its own failure status
        ("", "102", {"Gemini": 1}),  # a summary naming no run never covers a named run
        ("101", "", {}),  # a status naming no run: matched by commit
    ],
    ids=["failed-rerun", "partial-review", "summary-without-run", "status-without-run"],
)
def test_a_failed_run_is_measured_only_by_its_own_summary(
    summary_run: str, status_run: str, unmeasured: dict[str, int]
) -> None:
    """The summary and the status are matched by run id, never by time."""
    node = _node(
        9,
        reviews=[_summary("a", summary_run)],
        commits=[_commit("a", run=status_run, Gemini="FAILURE")],
    )
    pr = metrics.pull_request_from(node, NAMES)
    assert pr.unmeasured_runs == unmeasured
    assert pr.tokens == {"Gemini": [500, 50, 0, 0]}


def test_failed_attempts_are_read_and_failed_runs_without_a_summary_are_unmeasured() -> None:
    """``failed=N`` is summed; a failed or unfinished run with no summary on its commit is named."""
    marker = "<!-- model-usage: requests=2 input=900 output=90 unreported=0 failed=3 -->"
    reviews = [
        {
            "author": {"login": "github-actions"},
            "body": f"<!-- gemini-review findings=0 -->\n{marker}",
            "commit": {"oid": "a"},
        }
    ]
    commits = [
        _commit("a", Gemini="FAILURE"),  # failed, but its summary was posted: measured
        _commit("b", Gemini="ERROR", Reflection="PENDING"),
        _commit("c", Gemini="SUCCESS", Reflection="FAILURE"),
    ]
    pr = metrics.pull_request_from(_node(8, reviews=reviews, commits=commits), NAMES)
    assert pr.tokens == {"Gemini": [900, 90, 0, 3]}
    assert pr.unmeasured_runs == {"Gemini": 1, "Reflection": 2}

    data = metrics.summarise([pr], NAMES)
    gem = data["reviewers"]["Gemini"]
    assert (gem["failed_attempts"], gem["unmeasured_runs"]) == (3, 1)
    text = metrics.render(data, T0, T0 + timedelta(days=7))
    assert "| 900 (low) | 90 (low) |" in text  # the total is never shown as complete
    assert "Gemini 0 summary(ies) without a token marker, 0 request(s) with no reported" in text
    assert (
        "3 failed attempt(s), 1 failed or unfinished review run(s) with no summary from that run"
        in text
    )
    assert "Reflection 0 summary(ies)" in text and "2 failed or unfinished" in text


def test_more_than_one_page_marks_the_pull_request_as_truncated() -> None:
    """Threads or comments beyond the first page are not counted, and the PR is named."""
    long_thread = _thread(_gemini())
    long_thread["comments"]["pageInfo"]["hasNextPage"] = True
    assert metrics.pull_request_from(_node(3, long_thread), NAMES).truncated
    assert metrics.pull_request_from(_node(4, more_threads=True), NAMES).truncated
    many = _node(9)
    many["commits"]["pageInfo"] = {"hasPreviousPage": True}  # over 100 commits
    assert metrics.pull_request_from(many, NAMES).truncated
    assert not metrics.pull_request_from(_node(5), NAMES).truncated


def test_a_finding_only_one_reviewer_raised_is_unique() -> None:
    """Another reviewer's thread within three lines on the same file makes it shared."""
    pr = _from(
        _node(
            6,
            _thread(_gemini(10), _reply("accepted: yes"), line=10),
            _thread(_greptile(), _reply("accepted: yes"), line=12),
            _thread(_gemini(50), _reply("accepted: yes"), line=50),
            _thread(_gemini(80, "b.py"), _reply("rejected: no"), path="b.py", line=80),
        )
    )
    assert metrics.unique_accepted(pr.threads) == {"Gemini": 1}


# --- paging ------------------------------------------------------------------------


def test_every_search_page_is_read_and_only_the_window_is_kept() -> None:
    """Pages are followed by cursor; a merge outside the window is left out."""
    pages = [
        {"pageInfo": {"hasNextPage": True, "endCursor": "c1"}, "nodes": [_node(1)]},
        {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [_node(2, merged="2026-12-30T00:00:00Z"), {}],
        },
    ]
    afters: list[object] = []

    def query(text: str, variables: Mapping[str, Any]) -> dict[str, Any]:
        """Answer each page in turn, recording the cursor asked for."""
        afters.append(variables["after"])
        return {"search": pages.pop(0)}

    found = metrics.merged_pull_requests(query, "o/r", T0, T0 + timedelta(days=7), NAMES)
    assert [pr.number for pr in found] == [1] and afters == [None, "c1"]


# --- figures ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("values", "median", "p75"),
    [([], None, None), ([5.0], 5.0, 5.0), ([1.0, 2.0, 3.0, 4.0], 2.5, 3.0)],
    ids=["none", "one", "four"],
)
def test_percentiles_use_nearest_rank_and_say_none_when_empty(
    values: list[float], median: float | None, p75: float | None
) -> None:
    """No values is "not measured", never 0; p75 of four values is the third."""
    assert metrics._median(values) == median and metrics._p75(values) == p75


# --- the report --------------------------------------------------------------------


def test_the_report_names_every_figure_and_every_exclusion() -> None:
    """Precision, unmeasured tokens, truncated PRs and unmeasured CI minutes all appear."""
    prs = [
        _from(
            _node(
                7,
                _thread(_gemini(), _reply("accepted: yes")),
                _thread(_gemini(40), _reply("rejected: no"), line=40),
                more_threads=True,
                additions=1200,
            )
        )
    ]
    data = metrics.summarise(prs, NAMES)

    gem = data["reviewers"]["Gemini"]
    assert (gem["accepted"], gem["rejected"], gem["precision"]) == (1, 1, 0.5)
    assert data["reviewers"]["Reflection"]["precision"] is None  # not judged, not 0
    assert data["over_max_lines"] == [7] and data["labelled_share"] == 1.0
    text = metrics.render(data, T0, T0 + timedelta(days=7))
    assert "only the first page counted): #7" in text
    assert "Escaped defects: not measured." in text
    assert "CI minutes: not measured." in text
    assert "| Reflection | 0 | - | 0 | 0 | 0 | n/a |" in text
    assert "| 0-199 | 0 | n/a | n/a | n/a |" in text  # an empty bucket is not measured
    assert "Reflection 0 summary(ies) without a token marker" in text


def test_the_saved_json_records_its_period(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The JSON names the UTC window it covers, so saved reports can be compared."""
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setattr(metrics, "merged_pull_requests", lambda *_: [])
    out = tmp_path / "m.json"
    (tmp_path / "devloop.toml").write_text('[framework]\nversion = "0.3"\n')

    assert metrics.main(tmp_path, 3, out, {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "o/r"}) == 0

    data = json.loads(out.read_text())
    since, until = datetime.fromisoformat(data["since"]), datetime.fromisoformat(data["until"])
    assert until - since == timedelta(days=3) and since.tzinfo is not None


def _tool_commit(oid: str, agent: str = "") -> dict[str, Any]:
    """Return a commit node whose message carries ``Agent: agent`` when given."""
    trailer = f"\n\nAgent: {agent}" if agent else ""
    return {"commit": {"oid": oid, "message": f"feat: x{trailer}", "status": {"contexts": []}}}


def test_figures_are_reported_per_authoring_tool() -> None:
    summary = {"author": {"login": "github-actions"}, "body": "<!-- gemini-review findings=1 -->"}
    prs = [
        _from(
            _node(
                1,
                _thread(_gemini(), _reply("rejected: no")),
                commits=[_tool_commit("a", "codex/gpt-5"), _tool_commit("b", "codex/gpt-5")],
                reviews=[{**summary, "commit": {"oid": "a"}}, {**summary, "commit": {"oid": "b"}}],
            )
        ),
        _from(_node(2, commits=[_tool_commit("c", "claude-code/opus-5.5")], additions=900)),
        _from(
            _node(3, commits=[_tool_commit("d", "codex/gpt-5"), _tool_commit("e", "claude-code/x")])
        ),
        _from(_node(4, commits=[_tool_commit("f")])),
    ]
    assert [pr.agent for pr in prs] == ["codex", "claude-code", "mixed", "unknown"]
    assert prs[0].rounds == 2
    data = metrics.summarise(prs, NAMES)
    codex = data["by_tool"]["codex"]
    assert (codex["prs"], codex["median_rounds"], codex["rejected_share"]) == (1, 2, 1.0)
    assert data["by_tool"]["claude-code"]["over_max_lines"] == 1
    assert data["by_tool"]["unknown"]["rejected_share"] is None  # nothing judged, not 0
    text = metrics.render(data, T0, T0 + timedelta(days=7))
    assert "## By authoring tool" in text
    assert "| codex | 1 | 2.0 | 1.0 | 2.0 | 1.0 | 100.0 | 0 |" in text


def test_one_failed_status_per_reviewer_and_commit_is_counted_once() -> None:
    commit = _commit("a", run="5", Gemini="FAILURE")
    contexts = commit["commit"]["status"]["contexts"] * 2
    twice = {"commit": {**commit["commit"], "status": {"contexts": contexts}}}
    assert _from(_node(9, commits=[twice])).unmeasured_runs == {"Gemini": 1}


def test_a_pull_request_on_a_bucket_boundary_is_in_exactly_one_bucket() -> None:
    sizes = metrics.summarise([_from(_node(1, additions=200))], NAMES)["sizes"]
    assert [s["lines"] for s in sizes if s["prs"]] == ["200-799"]
    assert sum(s["prs"] for s in sizes) == 1

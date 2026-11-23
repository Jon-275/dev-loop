"""Regressions found by the local code and security review of the review port."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from review_helpers import PROVIDER, commit, run

from devloop.config import REVIEWED_FILES, parse_config
from devloop.review import github as gh
from devloop.review import run as r
from devloop.review.body import review_body
from devloop.review.gitread import run_git_diff
from devloop.review.model import Finding, Review, ReviewError, Settings, ThreadMark
from devloop.review.threads import fingerprint_marker, mark_for

POLICY = r.Policy(files=REVIEWED_FILES)


def _review(
    repo: Path, answer: str, policy: r.Policy = POLICY, **known: ThreadMark
) -> tuple[str, list[tuple[str, list[dict[str, object]]]]]:
    """Run one review of ``repo``'s last commit against its parent."""
    posted: list[tuple[str, list[dict[str, object]]]] = []
    log = r.run_review(
        Settings(
            "k", "t", "o/r", "7", run(repo, "rev-parse", "HEAD~1"), run(repo, "rev-parse", "HEAD")
        ),
        PROVIDER,
        policy,
        name="testbot",
        ask=lambda prompt, effort: answer,
        known=lambda: set(known.values()),
        post=lambda body, comments: posted.append((body, comments)),
    )
    return log, posted


def test_the_pull_requests_attributes_cannot_turn_its_code_into_a_binary_diff(repo: Path) -> None:
    (repo / ".gitattributes").write_text("*.py -diff\n")
    (repo / "evil.py").write_text("import os\nos.system('x')\n")
    head = commit(repo, "hide")
    diff = run_git_diff([f"{run(repo, 'rev-parse', 'HEAD~1')}...{head}", "--", "*.py"])
    assert "+os.system('x')" in diff and "Binary files" not in diff


def test_a_file_whose_diff_and_contents_both_miss_the_prompt_is_unreviewed(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(r, "MAX_FILES", 0)  # no contents attached
    (repo / "keep.py").chmod(0o755)  # a mode-only change: no hunks
    commit(repo, "chmod")
    reply = json.dumps({"summary": "s", "findings": [], "not_posted": []})
    with pytest.raises(ReviewError, match="1 of 1 changed file"):
        _review(repo, reply)


@pytest.mark.parametrize("name", ["gpt5", "my-llm"])
def test_reviewer_names_with_digits_and_hyphens_keep_their_markers(name: str) -> None:
    from dataclasses import replace

    other = replace(PROVIDER, name="Other", marker_prefix=f"{name}-finding")
    marker = fingerprint_marker(other, Finding("P2", "a.py", 3, "t", "b"))
    comment = {
        "id": 1,
        "user": {"login": "github-actions[bot]"},
        "body": marker,
        "path": "a.py",
        "line": 3,
    }
    assert mark_for(PROVIDER, comment, {f"{name}-finding": "Other"})[0].reviewer == "Other"
    summary = (
        f"<!-- {name}-review findings=0 posted=0 not_posted=0 unreviewed=0 base={'b' * 40} -->"
    )
    assert gh._SUMMARY.search(summary) and gh._ANY_SUMMARY.search(summary)


def test_a_moved_tier_one_file_keeps_its_tier(repo: Path) -> None:
    (repo / "src").mkdir()
    (repo / "src" / "auth.py").write_text("x = 1\n")
    commit(repo, "auth")
    run(repo, "mv", "src/auth.py", "misc.py")
    commit(repo, "move it out")
    policy = r.Policy(files=REVIEWED_FILES, tiers={1: ("src/*",)}, routes={1: ("testbot",)})
    reply = json.dumps({"summary": "s", "findings": [], "not_posted": []})
    log, _ = _review(repo, reply, policy)
    assert not log.startswith("Not routed")


def test_a_config_written_for_0_2_still_loads() -> None:
    old = '[framework]\nversion = "0.2"\n[review]\nblocking_severities = ["P0", "P1", "P2"]\n'
    assert parse_config(old).review.blocking_severities == ("P0", "P1", "P2")


def test_anthropic_is_not_offered_until_it_can_run() -> None:
    with pytest.raises(Exception, match="kind"):
        parse_config('[framework]\nversion = "0.3"\n[review.reviewers.x]\nkind = "anthropic"\n')


def test_every_status_name_is_the_reviewer_name_capitalised_so_the_workflow_can_name_it() -> None:
    from devloop.review.providers import BUILT_IN, provider_for

    for name, reviewer in BUILT_IN.items():
        assert provider_for(reviewer, "").status_context == f"{name.capitalize()} review"


def test_a_listing_cut_at_its_page_limit_fails_instead_of_reading_short() -> None:
    settings = Settings("k", "t", "o/r", "7", "a", "b")
    pages = iter([gh.Reply(200, [1], "https://api.github.com/next")] * 3)
    with pytest.raises(ReviewError, match="more than 2 pages"):
        gh._get_all(
            settings, "https://api.github.com/x", "comments", lambda *a: next(pages), pages=2
        )


def test_a_dropped_github_read_is_a_review_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import http.client
    import urllib.request

    def urlopen(request: urllib.request.Request, timeout: float) -> None:
        """Drop the connection mid-body."""
        raise http.client.IncompleteRead(b"")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(ReviewError, match="IncompleteRead"):
        gh.http("GET", "https://api.github.com/x", "t")


def test_a_p1_another_reviewer_raised_follows_the_blocking_severities_too(repo: Path) -> None:
    (repo / "keep.py").write_text("x = 2\n")
    commit(repo, "edit")
    other = ThreadMark("Greptile", path="keep.py", line=1, url="u")
    finding = {"severity": "P1", "path": "keep.py", "line": 1, "title": "t", "body": "b"}
    reply = json.dumps({"summary": "s", "findings": [finding], "not_posted": []})
    _, [(_, comments)] = _review(
        repo, reply, r.Policy(files=REVIEWED_FILES, blocking=("P2",)), g=other
    )
    assert comments == []


def test_the_summary_never_says_a_summary_only_finding_is_a_thread() -> None:
    nit = Finding("P3", "a.py", 1, "Nit", "b")
    body = review_body(PROVIDER, Review("s", (nit,)), "low", 0, summary_only=[nit])
    assert "Each one is an inline thread" not in body
    assert "Each blocking one is an inline thread" in body

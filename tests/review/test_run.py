"""One whole review run on a real repository, with the model, threads and GitHub faked."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from review_helpers import PROVIDER, commit, run

from devloop.config import REVIEWED_FILES
from devloop.review import parts
from devloop.review import run as r
from devloop.review.model import (
    Effort,
    Finding,
    IncompleteReview,
    NotSent,
    ReplyCutOff,
    ReviewError,
    Settings,
    ThreadMark,
    Usage,
)
from devloop.review.threads import Feedback, Suppression, legacy_fingerprint

POLICY = r.Policy(files=REVIEWED_FILES)


def _finding(path: str, title: str = "Bug", severity: str = "P2", line: int = 2) -> dict[str, Any]:
    """Return one finding in the reply's JSON shape."""
    return {"severity": severity, "path": path, "line": line, "title": title, "body": "b"}


def _reply(*findings: dict[str, Any], summary: str = "Adds a sync.", **extra: object) -> str:
    """Return a reply in the expected JSON shape."""
    return json.dumps({"summary": summary, "findings": list(findings), "not_posted": [], **extra})


def _files_in(prompt: str) -> list[str]:
    """Return the files whose diff sections a prompt carries."""
    return [
        line.split(" b/", 1)[1] for line in prompt.splitlines() if line.startswith("diff --git ")
    ]


@dataclass
class Run:
    """A pull request adding ``paths`` on a real repository, and one review of it."""

    repo: Path
    paths: list[str]
    answer: Callable[[str, Effort], str]
    known: set[ThreadMark] = field(default_factory=set)
    asked: list[tuple[str, Effort]] = field(default_factory=list)
    posted: list[tuple[str, list[dict[str, Any]]]] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Commit the pull request's files on top of the base."""
        self.base = run(self.repo, "rev-parse", "HEAD")
        for path in self.paths:
            (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / path).write_text("a = 1\nb = 2\n")
        self.head = commit(self.repo, "the pull request")

    def ask(self, prompt: str, effort: Effort) -> str:
        """Record the request and answer it."""
        self.asked.append((prompt, effort))
        return self.answer(prompt, effort)

    def __call__(
        self,
        policy: r.Policy = POLICY,
        *,
        feedback: Feedback | None = None,
        usage: Usage | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> str:
        """Run the review and return its log line."""
        return r.run_review(
            Settings("k", "t", "o/r", "7", self.base, self.head),
            PROVIDER,
            policy,
            name="testbot",
            ask=self.ask,
            known=lambda: self.known,
            post=lambda body, comments: self.posted.append((body, comments)),
            feedback=feedback,
            usage=usage,
            clock=clock,
        )

    @property
    def body(self) -> str:
        """Return the one posted review's top-level comment."""
        [(body, _)] = self.posted
        return body


def test_each_blocking_finding_is_a_thread_and_a_p3_only_a_summary_line(repo: Path) -> None:
    reply = _reply(_finding("app.py", "Leak", "P1"), _finding("app.py", "Vague name", "P3"))
    review = Run(repo, ["app.py"], lambda prompt, effort: reply)
    assert review() == "Posted Testbot review with 1 finding thread(s)."
    [(body, comments)] = review.posted
    assert [c["body"].splitlines()[0] for c in comments] == ["**P1 — Leak**"]
    assert "Not blocking (P3), so listed here without a thread" in body
    assert "- P3 `app.py:2` Vague name" in body
    assert "findings=2 posted=1" in body


def test_blocking_severities_come_from_the_policy(repo: Path) -> None:
    review = Run(repo, ["app.py"], lambda p, e: _reply(_finding("app.py", "Nit", "P3")))
    review(r.Policy(files=REVIEWED_FILES, blocking=("P1", "P2", "P3")))
    [(_, comments)] = review.posted
    assert len(comments) == 1


def test_a_pull_request_with_no_reviewed_file_types_asks_nothing(repo: Path) -> None:
    review = Run(repo, ["docs/guide.md"], lambda p, e: pytest.fail("asked"))
    assert review() == "No reviewed file types changed; nothing for Testbot to review."
    assert review.posted == []


def test_a_missing_key_fails_the_check_by_name(repo: Path) -> None:
    review = Run(repo, ["app.py"], lambda p, e: _reply())
    with pytest.raises(ReviewError, match="TESTBOT_API_KEY is not available"):
        r.run_review(
            Settings("", "t", "o/r", "7", review.base, review.head),
            PROVIDER,
            POLICY,
            name="testbot",
            ask=review.ask,
            known=set,
            post=lambda body, comments: None,
        )


@pytest.mark.parametrize(
    ("paths", "tier", "asked"),
    [(["docs/a.md"], 3, False), (["app.py"], 2, True), (["src/crm_sync.py", "docs/a.md"], 1, True)],
)
def test_the_tier_routes_the_reviewer(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    paths: list[str],
    tier: int,
    asked: bool,
) -> None:
    out = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    policy = r.Policy(
        files=REVIEWED_FILES + ("*.md",),
        tiers={1: ("src/crm_*",), 3: ("docs/*",)},
        routes={1: ("testbot",), 2: ("testbot",), 3: ("greptile",)},
    )
    review = Run(repo, paths, lambda p, e: _reply())
    log = review(policy)
    assert r.pr_tier(paths, policy.tiers) == tier
    assert bool(review.asked) is asked
    if not asked:
        assert log == "Not routed: tier 3 pull requests get no Testbot review."
        assert out.read_text() == f"status_description={log[:-1]}\n"


def test_effort_follows_the_high_effort_globs(repo: Path) -> None:
    review = Run(repo, ["scripts/tool.py"], lambda p, e: _reply())
    review(r.Policy(files=REVIEWED_FILES, high_effort=("*model*",)))
    assert [effort for _, effort in review.asked] == ["low"]


def test_a_rerun_does_not_reopen_a_finding_that_already_has_a_thread(repo: Path) -> None:
    from devloop.review.threads import fingerprint

    held = Finding("P2", "app.py", 2, "Leak", "b")
    mark = ThreadMark("Testbot", key=fingerprint(held), line=2, title="leak")
    review = Run(repo, ["app.py"], lambda p, e: _reply(_finding("app.py", "Leak")), {mark})
    assert review() == "Posted Testbot review with 0 finding thread(s)."
    assert "_1 of them already have a thread from an earlier Testbot review" in review.body


def test_a_p1_beside_another_reviewers_thread_still_gets_its_thread(repo: Path) -> None:
    other = ThreadMark("Greptile", path="app.py", line=1, url="https://g/1")
    reply = _reply(_finding("app.py", "Token logged", "P1"), _finding("app.py", "Nit", "P2"))
    review = Run(repo, ["app.py"], lambda p, e: reply, {other})
    review()
    [(body, comments)] = review.posted
    assert len(comments) == 1
    assert comments[0]["body"].endswith("_Also raised by [Greptile's thread](https://g/1)._")
    assert "Token logged: also raised by [Greptile's thread](https://g/1); posted too" in body
    assert "P2 `app.py:2` Nit: also raised by" in body


def test_a_suppressed_finding_is_named_and_a_p1_is_never_suppressed(repo: Path) -> None:
    noisy, severe = (
        Finding("P2", "app.py", 2, "Noisy", "b"),
        Finding("P1", "app.py", 2, "Severe", "b"),
    )
    feedback = Feedback(
        suppressed={
            legacy_fingerprint(f): Suppression(legacy_fingerprint(f), 4, "u")
            for f in (noisy, severe)
        }
    )
    reply = _reply(_finding("app.py", "Noisy"), _finding("app.py", "Severe", "P1"))
    review = Run(repo, ["app.py"], lambda p, e: reply)
    review(feedback=feedback)
    [(body, comments)] = review.posted
    assert len(comments) == 1 and "Severe" in comments[0]["body"]
    assert "P2 `app.py:2` Noisy: suppressed (rejected 4 times, [last rejection](u))." in body


def test_usage_is_reported_at_the_end_of_the_summary(repo: Path) -> None:
    usage = Usage(run="9")
    usage.add(100, 20)
    review = Run(repo, ["app.py"], lambda p, e: _reply())
    review(usage=usage)
    assert "<!-- model-usage: requests=1 input=100 output=20 unreported=0 failed=0 run=9 -->" in (
        review.body
    )


def test_a_cut_off_review_is_asked_again_file_by_file_and_completes(repo: Path) -> None:
    paths = ["a_sync.py", "b_sync.py", "c_sync.py"]

    def answer(prompt: str, effort: Effort) -> str:
        """Cut off when more than one file is asked; else one finding per file."""
        files = _files_in(prompt)
        if len(files) > 1:
            raise ReplyCutOff("cut off")
        return _reply(_finding(files[0], f"Problem in {files[0]}"), summary=f"Reviewed {files[0]}.")

    review = Run(repo, paths, answer)
    assert review() == "Posted Testbot review with 3 finding thread(s)."
    assert [_files_in(prompt) for prompt, _ in review.asked] == [
        paths,
        ["a_sync.py"],
        ["b_sync.py", "c_sync.py"],
        ["b_sync.py"],
        ["c_sync.py"],
    ]
    assert "Review only these files: b_sync.py" in review.asked[3][0]
    assert (
        "reviewed in 3 parts" in review.body and "- `a_sync.py`: Reviewed a_sync.py." in review.body
    )


def test_a_file_that_never_fits_is_named_and_the_check_fails(
    repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def answer(prompt: str, effort: Effort) -> str:
        """``big_sync.py`` never fits; ``small_sync.py`` is clean."""
        if "big_sync.py" in _files_in(prompt):
            raise ReplyCutOff("cut off")
        return _reply(summary="Small change.")

    review = Run(repo, ["big_sync.py", "small_sync.py"], answer)
    out = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert r.outcome(PROVIDER, review) == 1
    assert [(_files_in(p), e) for p, e in review.asked][-3:] == [
        (["big_sync.py"], "high"),
        (["big_sync.py"], "low"),
        (["small_sync.py"], "high"),
    ]
    assert f"- `big_sync.py`: {parts.CUT_OFF_REASON}" in review.body
    assert "**No findings in the files that were reviewed.**" in review.body
    assert capsys.readouterr().out.startswith(
        "::error title=Testbot review::Testbot posted a partial"
    )
    assert out.read_text() == (
        "status_description=Incomplete: 1 of 2 changed files not reviewed; see the comment\n"
    )


def test_parts_past_the_time_budget_are_named_as_unreviewed(repo: Path) -> None:
    readings = [0.0]

    def clock() -> float:
        """Read 0 s, then 1,500 s: no further request fits after the first."""
        reading = readings[-1]
        readings.append(1500.0)
        return reading

    def answer(prompt: str, effort: Effort) -> str:
        """Cut off for the whole diff."""
        if len(_files_in(prompt)) > 1:
            raise ReplyCutOff("cut off")
        return _reply()

    review = Run(repo, ["a.py", "b.py"], answer)
    with pytest.raises(IncompleteReview, match="2 of 2 changed file"):
        review(clock=clock)
    assert len(review.asked) == 1
    assert f"- `a.py`: {parts.OUT_OF_TIME_REASON}" in review.body


def test_a_finding_two_parts_report_is_posted_once_and_named(repo: Path) -> None:
    def answer(prompt: str, effort: Effort) -> str:
        """Both single-file parts report the same finding in ``shared.py``."""
        if len(_files_in(prompt)) > 1:
            raise ReplyCutOff("cut off")
        return _reply(_finding("shared.py", "Shared helper drops the CRM id"))

    review = Run(repo, ["shared.py", "user.py"], answer)
    review()
    [(_, comments)] = review.posted
    assert len(comments) == 1
    assert "1 finding(s) were reported by more than one part" in review.body


def test_an_unreadable_reply_fails_the_check_and_posts_nothing(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    review = Run(repo, ["app.py"], lambda p, e: "not json")
    assert r.outcome(PROVIDER, review) == 1 and review.posted == []
    assert capsys.readouterr().out.startswith(
        "::error title=Testbot review::Testbot's reply is not JSON"
    )


# Built at run time, so this file holds no string in a key's own format.
_AWS_KEY = "AKIA" + "ABCDEFGHIJKLMNOP"


def _with_secret(repo: Path, path: str = "src/sync.py") -> Run:
    """Return a pull request whose one file holds a likely secret."""
    pull = Run(repo, [path], lambda prompt, effort: _reply())
    (repo / path).write_text(f"a = 1\nKEY = '{_AWS_KEY}'\n")
    pull.head = commit(repo, "a key by mistake")
    return pull


def test_a_likely_secret_stops_the_review_before_anything_is_sent(repo: Path) -> None:
    """Nothing is asked and nothing is posted; the error names the place, never the key."""
    pull = _with_secret(repo)
    with pytest.raises(NotSent) as error:
        pull()
    assert pull.asked == [] and pull.posted == []
    assert "src/sync.py:2 (AWS access key id)" in str(error.value)
    assert _AWS_KEY not in str(error.value) and _AWS_KEY not in error.value.status
    assert error.value.status == "Not sent: 1 likely secret(s) in 1 file(s); see the run"


def test_a_secret_in_an_allowed_file_or_with_the_scan_off_is_sent(repo: Path) -> None:
    """The exemption and the switch both let the review run as before."""
    pull = _with_secret(repo, "tests/fixtures/keys.py")
    pull(r.Policy(files=REVIEWED_FILES, secret_allow=("tests/fixtures/*",)))
    assert len(pull.asked) == 1
    pull(r.Policy(files=REVIEWED_FILES, secret_scan=False))
    assert len(pull.asked) == 2


def test_the_failed_check_reports_the_secret_count_as_its_status(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The workflow gets a status of counts only, and the log gets one annotation."""
    output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    assert r.outcome(PROVIDER, _with_secret(repo)) == 1
    assert output.read_text() == (
        "status_description=Not sent: 1 likely secret(s) in 1 file(s); see the run\n"
    )
    printed = capsys.readouterr().out
    assert printed.startswith("::error ") and _AWS_KEY not in printed


def test_an_outside_pull_request_over_the_limit_sends_nothing(repo: Path) -> None:
    """Over ``max_chars`` nothing is asked; under it, or with no limit, the review runs."""
    pull = Run(repo, ["src/sync.py"], lambda prompt, effort: _reply())
    with pytest.raises(NotSent) as error:
        pull(r.Policy(files=REVIEWED_FILES, max_chars=10))
    assert pull.asked == [] and pull.posted == []
    assert "over review.outside_max_chars (10)" in str(error.value)
    assert error.value.status.startswith("Not sent: ") and "limit of 10" in error.value.status
    pull(r.Policy(files=REVIEWED_FILES, max_chars=100_000))
    pull(r.Policy(files=REVIEWED_FILES))
    assert len(pull.asked) == 2


def test_an_annotation_stays_one_workflow_command() -> None:
    assert r.annotation(PROVIDER, "a\nb%\r::warning::x") == (
        "::error title=Testbot review::a%0Ab%25%0D::warning::x"
    )

"""Tests for the council's report and the Council review verdict (devloop.council_report).

The invariant: only a report on the pull request's current head, at its tier or riskier,
from every reviewer the change summons, with a safe verdict and no P0 or P1, is a pass.
Anything not judged is pending, never a pass, and anything unreadable is an error.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest
from conftest import git

from devloop.council import Plan
from devloop.council_report import Report, ReportError, judge, parse_report

HEAD = "a" * 40
OTHER = "b" * 40
PLAN = Plan(tier=1, roles=(), reviewers={"core": ("architect",), "security": ("security",)})


def _text(**overrides: str) -> str:
    """Return a report whose frontmatter fields are overridden by ``overrides``."""
    fields = {
        "pr": "12",
        "head": f'"{HEAD}"',
        "tier": "1",
        "reviewers": '["core", "security"]',
        "verdict": '"safe"',
        "findings": '{"P0": 0, "P1": 0, "P2": 2, "P3": 1}',
    }
    fields.update(overrides)
    lines = [f"{key}: {value}" for key, value in fields.items() if value != ""]
    return "---\n" + "\n".join(lines) + "\n---\n\n**SAFE.** Nothing blocks.\n"


def _report(**overrides: str) -> Report:
    """Parse a report with ``overrides``."""
    return parse_report(_text(**overrides))


def test_a_safe_report_on_the_head_from_every_reviewer_passes() -> None:
    """The one passing case."""
    verdict = judge(_report(), HEAD, PLAN, required=(1,))
    assert verdict.state == "success"
    assert "P2 2" in verdict.description and "P3 1" in verdict.description


def test_a_tier_that_needs_no_council_passes_without_a_report() -> None:
    """Tiers outside require_status never wait for a council."""
    plan = Plan(tier=2, roles=(), reviewers={"core": ("architect",)})
    verdict = judge(None, HEAD, plan, required=(1,))
    assert verdict.state == "success"
    assert "not required" in verdict.description


@pytest.mark.parametrize(
    ("report", "reason"),
    [
        (None, "no council report"),
        (_text(head=f'"{OTHER}"'), "bbbbbbb"),
        (_text(tier="2"), "tier 2"),
        (_text(reviewers='["core"]'), "security"),
    ],
)
def test_anything_not_judged_is_pending(report: str | None, reason: str) -> None:
    """No report, a stale head, a lower tier or a missing reviewer is not a verdict."""
    parsed = parse_report(report) if report is not None else None
    verdict = judge(parsed, HEAD, PLAN, required=(1,))
    assert verdict.state == "pending"
    assert reason in verdict.description


@pytest.mark.parametrize(
    "overrides",
    [
        {"verdict": '"not safe"'},
        {"findings": '{"P0": 1, "P1": 0, "P2": 0, "P3": 0}'},
        {"findings": '{"P0": 0, "P1": 2, "P2": 0, "P3": 0}'},
    ],
)
def test_a_not_safe_verdict_or_a_p0_or_p1_fails(overrides: dict[str, str]) -> None:
    """A safe verdict with a P0 or P1 still fails: the counts are the evidence."""
    verdict = judge(_report(**overrides), HEAD, PLAN, required=(1,))
    assert verdict.state == "failure"


def test_only_one_combination_ever_passes() -> None:
    """Over every combination of head, tier, reviewers, verdict and findings, one passes."""
    heads = [f'"{HEAD}"', f'"{OTHER}"']
    tiers = ["1", "2"]
    reviewers = ['["core", "security"]', '["core"]', '["core", "security", "extra"]']
    verdicts = ['"safe"', '"not safe"']
    findings = [
        '{"P0": 0, "P1": 0, "P2": 0, "P3": 0}',
        '{"P0": 1, "P1": 0, "P2": 0, "P3": 0}',
        '{"P0": 0, "P1": 1, "P2": 0, "P3": 0}',
    ]
    passing = []
    for combo in itertools.product(heads, tiers, reviewers, verdicts, findings):
        head, tier, who, said, found = combo
        report = _report(head=head, tier=tier, reviewers=who, verdict=said, findings=found)
        if judge(report, HEAD, PLAN, required=(1,)).state == "success":
            passing.append(combo)
    assert passing == [
        (heads[0], "1", reviewers[0], verdicts[0], findings[0]),
        (heads[0], "1", reviewers[2], verdicts[0], findings[0]),
    ]


def test_a_report_for_another_pull_request_is_pending() -> None:
    """One pull request's review never stands for another's, even on the same commit."""
    assert judge(_report(), HEAD, PLAN, required=(1,), pr=12).state == "success"
    verdict = judge(_report(), HEAD, PLAN, required=(1,), pr=15)
    assert verdict.state == "pending"
    assert "#12" in verdict.description


def test_a_riskier_tier_than_the_change_counts() -> None:
    """A tier-1 review of a tier-2 change in a project that requires tier 2 is enough."""
    plan = Plan(tier=2, roles=(), reviewers={"core": ("architect",)})
    verdict = judge(_report(reviewers='["core"]'), HEAD, plan, required=(1, 2))
    assert verdict.state == "success"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("no frontmatter\n", "must start with ---"),
        ("---\npr: 1\n", "never closed"),
        (_text(head='"short"'), "head"),
        (_text(verdict='"fine"'), "verdict"),
        (_text(findings='{"P0": -1}'), "findings"),
        (_text(findings='{"P5": 1}'), "findings"),
        (_text(tier="4"), "tier"),
        (_text(reviewers='"core"'), "reviewers"),
        (_text(owner='"me"'), "owner"),
        (_text(head=""), "head is missing"),
        (_text(findings='{"P2": 1}'), "P0, P1"),
        (_text(findings="{}"), "P0, P1"),
        (_text().replace('verdict: "safe"', 'verdict: "not safe"\nverdict: "safe"'), "twice"),
    ],
)
def test_a_malformed_report_is_an_error_never_a_verdict(text: str, message: str) -> None:
    """Every field is checked; a report that cannot be read never passes."""
    with pytest.raises(ReportError, match=message):
        parse_report(text)


def _repo(tmp_path: Path) -> Path:
    """Return a git repository with a devloop.toml, a council and one commit."""
    root = tmp_path / "repo"
    (root / "devloop" / "council").mkdir(parents=True)
    (root / "devloop.toml").write_text(
        '[framework]\nversion = "0.6"\n\n[tiers]\n1 = ["src/*"]\n', encoding="utf-8"
    )
    (root / "devloop" / "council" / "architect.md").write_text(
        '---\nname: architect\ntitle: Architect\nsummoned_by: ["**"]\ntiers: [1, 2]\n'
        "reviewer: core\n---\nMandate: m\nChecks: c\nSeverity anchors: s\nProof methods: p\n",
        encoding="utf-8",
    )
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "init")
    return root


def test_the_command_judges_a_report_on_this_checkout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``devloop council report`` judges the report against HEAD and exits by the verdict."""
    from devloop.council import main

    root = _repo(tmp_path)
    head = git(root, "rev-parse", "HEAD").strip()
    report = tmp_path / "review-pr12.md"
    report.write_text(_text(head=f'"{head}"', reviewers='["core"]'), encoding="utf-8")
    argv = ["report", "--root", str(root), "--report", str(report), "--paths", "src/app.py"]
    assert main(argv) == 0
    assert "success" in capsys.readouterr().out
    report.write_text(_text(reviewers='["core"]'), encoding="utf-8")  # a stale head
    assert main(argv) == 1
    assert "pending" in capsys.readouterr().out
    report.write_text("not a report\n", encoding="utf-8")
    assert main(argv) == 2


def test_head_judges_that_commit_s_change_not_the_checkout_s(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """With --head, the changed files and the head are that commit's, short refs included."""
    from devloop.council import main

    root = _repo(tmp_path)
    git(root, "checkout", "-q", "-b", "feature")
    (root / "src" / "app.py").write_text("x = 2\n", encoding="utf-8")
    git(root, "-c", "commit.gpgsign=false", "commit", "-q", "-am", "tier-1 change")
    tier1 = git(root, "rev-parse", "HEAD").strip()
    git(root, "checkout", "-q", "main")
    report = tmp_path / "review-pr12.md"
    report.write_text(_text(head=f'"{tier1}"', tier="2", reviewers='["core"]'), encoding="utf-8")
    argv = ["report", "--root", str(root), "--report", str(report), "--base", "main"]
    assert main([*argv, "--head", tier1[:10]]) == 1
    assert "change is tier 1" in capsys.readouterr().out

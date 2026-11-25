"""The review gate: who is reviewed at once, who waits, and that only the gate decides.

The end state is checked for every combination of gate setting, author, event and
label: the job that holds the key runs exactly when an independent statement of the
rule says it should.
"""

from __future__ import annotations

import itertools
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "review.yml"
APPROVAL = "ai-review-approved"
ASSOCIATIONS = (
    "OWNER",
    "MEMBER",
    "COLLABORATOR",
    "CONTRIBUTOR",
    "FIRST_TIME_CONTRIBUTOR",
    "FIRST_TIMER",
    "NONE",
    "",
)
ACTIONS = ("opened", "synchronize", "reopened", "ready_for_review", "labeled")
AUTHORS = ("a-stranger", "dependabot[bot]", "dependabot")
LABELS = (APPROVAL, "large-pr-approved", "")
TRUSTED = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})


def _jobs() -> dict[str, Any]:
    """Return the jobs of the shared review workflow."""
    jobs: dict[str, Any] = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    return jobs


def _decide(tmp_path: Path, **env: str) -> dict[str, str]:
    """Run the gate's own script with ``env``; return what it wrote to its outputs."""
    script = next(s for s in _jobs()["gate"]["steps"] if s.get("id") == "decide")["run"]
    output = tmp_path / "output"
    output.write_text("", encoding="utf-8")
    done = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        env={"PATH": "/usr/bin:/bin", "GITHUB_OUTPUT": str(output), **env},
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    found = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())
    found["exit"] = str(done.returncode)
    return found


def _reviewed(gate: str, association: str, action: str, author: str, label: str) -> bool:
    """Return whether the rule, stated apart from the script, reviews this event."""
    if gate != "members":
        return True
    if action == "labeled" and label != APPROVAL:
        return False
    insider = association in TRUSTED or author == "dependabot[bot]"
    return insider or action == "labeled"


def test_the_key_is_spent_exactly_when_the_rule_says(tmp_path: Path) -> None:
    """Every combination of setting, author, event and label ends in the right answer."""
    cases = itertools.product(("", "members"), ASSOCIATIONS, ACTIONS, AUTHORS, LABELS)
    for gate, association, action, author, label in cases:
        if action != "labeled" and label:
            continue  # only a labeled event carries a label
        found = _decide(
            tmp_path,
            GATE=gate,
            APPROVAL_LABEL=APPROVAL,
            TRUSTED_BOTS="dependabot[bot]",
            ASSOCIATION=association,
            AUTHOR=author,
            ACTION=action,
            LABEL=label,
        )
        case = (gate, association, action, author, label)
        assert found["exit"] == "0", case
        assert (found["run"] == "true") is _reviewed(*case), (case, found)


def test_an_outside_pull_request_is_never_reviewed_without_the_approval_label(
    tmp_path: Path,
) -> None:
    """The invariant by itself: no trusted author, no approval label, no review."""
    for association in ("CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER", "NONE", ""):
        for action, label in (
            ("opened", ""),
            ("synchronize", ""),
            ("reopened", ""),
            ("ready_for_review", ""),
            ("labeled", "large-pr-approved"),
            ("labeled", ""),
        ):
            found = _decide(
                tmp_path,
                GATE="members",
                APPROVAL_LABEL=APPROVAL,
                TRUSTED_BOTS="dependabot[bot]",
                ASSOCIATION=association,
                AUTHOR="a-stranger",
                ACTION=action,
                LABEL=label,
            )
            assert found["run"] == "false", (association, action, label)


def test_a_push_after_approval_waits_again(tmp_path: Path) -> None:
    """Approval covers the head it was given for: the next push is not reviewed."""
    common = {
        "GATE": "members",
        "APPROVAL_LABEL": APPROVAL,
        "TRUSTED_BOTS": "dependabot[bot]",
        "ASSOCIATION": "NONE",
        "AUTHOR": "a-stranger",
    }
    approved = _decide(tmp_path, ACTION="labeled", LABEL=APPROVAL, **common)
    pushed = _decide(tmp_path, ACTION="synchronize", LABEL="", **common)
    assert (approved["run"], approved["reason"]) == ("true", "approved")
    assert (pushed["run"], pushed["reason"]) == ("false", "waiting")


def test_another_label_changes_nothing_even_for_a_member(tmp_path: Path) -> None:
    """Adding an unrelated label neither reviews again nor marks the status waiting."""
    found = _decide(
        tmp_path,
        GATE="members",
        APPROVAL_LABEL=APPROVAL,
        TRUSTED_BOTS="",
        ASSOCIATION="OWNER",
        AUTHOR="the-owner",
        ACTION="labeled",
        LABEL="large-pr-approved",
    )
    assert (found["run"], found["reason"]) == ("false", "other-label")


def test_a_login_is_matched_whole_and_never_as_a_pattern(tmp_path: Path) -> None:
    """``dependabot[bot]`` trusts that login only, not ``dependabotb`` or ``dependabot``."""
    for author in ("dependabotb", "dependabot", "dependabot[bot]x", "*"):
        found = _decide(
            tmp_path,
            GATE="members",
            APPROVAL_LABEL=APPROVAL,
            TRUSTED_BOTS="dependabot[bot] *",
            ASSOCIATION="NONE",
            AUTHOR=author,
            ACTION="opened",
            LABEL="",
        )
        assert found["run"] == ("true" if author == "*" else "false"), author


def test_an_unknown_gate_setting_fails_and_reviews_nothing(tmp_path: Path) -> None:
    """A mistyped setting stops the run instead of reviewing everyone."""
    found = _decide(
        tmp_path,
        GATE="member",
        APPROVAL_LABEL=APPROVAL,
        TRUSTED_BOTS="",
        ASSOCIATION="NONE",
        AUTHOR="a-stranger",
        ACTION="opened",
        LABEL="",
    )
    assert found["exit"] != "0" and "run" not in found


def test_only_the_gate_lets_the_job_with_the_key_run() -> None:
    """The review job needs the gate's yes; the gate holds no key and reads no code."""
    jobs = _jobs()
    review, gate = jobs["review"], jobs["gate"]
    assert review["needs"] == "gate"
    assert review["if"] == "needs.gate.outputs.run == 'true'"
    assert "needs.gate.outputs.run" in review["concurrency"]["group"]
    assert gate["permissions"] == {"statuses": "write"}
    assert "secrets." not in str(gate)
    assert not any("uses" in step for step in gate["steps"])
    decide = next(s for s in gate["steps"] if s.get("id") == "decide")
    assert set(decide["env"].values()) == {
        "${{ inputs.gate }}",
        "${{ inputs.approval_label }}",
        "${{ inputs.trusted_bots }}",
        "${{ github.event.pull_request.author_association }}",
        "${{ github.event.pull_request.user.login }}",
        "${{ github.event.action }}",
        "${{ github.event.label.name }}",
    }


def test_a_waiting_pull_request_is_told_so_on_its_head_commit() -> None:
    """The status says who must act, and is posted only for a waiting pull request."""
    waiting = _jobs()["gate"]["steps"][1]
    assert waiting["if"] == "steps.decide.outputs.reason == 'waiting'"
    assert "state=pending" in waiting["run"]
    assert "Waiting for a maintainer to add the ${APPROVAL_LABEL} label" in waiting["run"]
    assert waiting["env"]["HEAD_SHA"] == "${{ github.event.pull_request.head.sha }}"


@pytest.mark.parametrize("gate", ["", "members"])
def test_the_default_is_unchanged_for_callers_that_set_no_gate(tmp_path: Path, gate: str) -> None:
    """With no gate every pull request is reviewed, as before; a member always is."""
    found = _decide(
        tmp_path,
        GATE=gate,
        APPROVAL_LABEL=APPROVAL,
        TRUSTED_BOTS="",
        ASSOCIATION="OWNER" if gate else "NONE",
        AUTHOR="someone",
        ACTION="opened",
        LABEL="",
    )
    assert found["run"] == "true"

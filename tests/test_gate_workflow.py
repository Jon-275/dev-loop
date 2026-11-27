"""The merge gate's workflow: what runs, with which token, and who may post the status.

Invariants. The job holds a token that can write a status, so it is pinned whole here:
an edit to a shell line, the environment or any other key fails a test. Only devloop
posts pending or a verdict; the shell posts one failure, for a run that broke. No other
workflow names the status.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from workflow_retry import GH_API

from devloop.config import load_config
from devloop.gate import rules

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
GATE = "./.github/workflows/gate.yml"
PERMISSIONS = {
    "checks": "read",
    "contents": "read",
    "pull-requests": "read",
    "statuses": "write",
}
NUMBER = "${{ github.event.pull_request.number || inputs.pr }}"
FIND_HEAD = (
    GH_API + 'case "$PR_NUMBER" in\n'
    "  ''|*[!0-9]*) echo \"::error title=Merge gate::PR_NUMBER must be a number\"; exit 1 ;;\n"
    "esac\n"
    "# The head as it is now, not as the event saw it: a run that waited its turn\n"
    "# judges the current head, and a failure must be reported on that commit.\n"
    'sha="$(gh_api "repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}" --jq .head.sha)"\n'
    'case "$sha" in\n'
    "  *[!0-9a-f]*|'') echo \"::error title=Merge gate::no head commit\"; exit 1 ;;\n"
    "esac\n"
    'echo "sha=${sha}" >> "$GITHUB_OUTPUT"\n'
)
#: The gate's whole job, word for word.
GATE_JOB: dict[str, Any] = {
    "name": "Merge gate run",
    "runs-on": "ubuntu-24.04",
    "timeout-minutes": 10,
    "permissions": PERMISSIONS,
    # One evaluation of a pull request at a time; a run is never stopped part-way.
    "concurrency": {
        "group": "merge-gate-${{ github.repository }}-${{ inputs.context }}-" + NUMBER,
        "cancel-in-progress": False,
    },
    "env": {
        "GITHUB_TOKEN": "${{ github.token }}",
        "GH_TOKEN": "${{ github.token }}",
        "PR_NUMBER": NUMBER,
        "RUN_URL": (
            "${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
        ),
    },
    "steps": [
        {"name": "Find the head commit", "id": "head", "run": FIND_HEAD},
        {
            "name": "Check out the default branch (the code and settings that run)",
            "uses": "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            "with": {
                "ref": "${{ github.event.repository.default_branch }}",
                "persist-credentials": False,
            },
        },
        {
            "uses": "astral-sh/setup-uv@1db35dd8eee11c5894872e0b383ae6c406cf738d",
            "with": {"version": "0.13.7", "python-version": "3.12.14", "enable-cache": False},
        },
        {
            "name": "Install devloop",
            "env": {"DEVLOOP": "${{ inputs.devloop }}"},
            "run": 'uv tool install "$DEVLOOP"',
        },
        {
            "name": "Decide and post the status",
            "id": "gate",
            "timeout-minutes": 5,
            "env": {"GATE_CONTEXT": "${{ inputs.context }}"},
            "run": (
                'devloop gate --repo "$GITHUB_REPOSITORY" --pr "$PR_NUMBER" --run-url "$RUN_URL" '
                '--context "$GATE_CONTEXT"'
            ),
        },
        {
            "name": "Report a run that could not check",
            # The head devloop judged, when it got that far: a run that waited may have
            # judged a newer head than the first step found.
            "if": ("failure() && (steps.gate.outputs.sha != '' || steps.head.outputs.sha != '')"),
            "env": {
                "SHA": "${{ steps.gate.outputs.sha || steps.head.outputs.sha }}",
                "CONTEXT": "${{ inputs.context }}",
            },
            "run": (
                GH_API + 'gh_api "repos/${GITHUB_REPOSITORY}/statuses/${SHA}" -f state=failure \\\n'
                '  -f "context=${CONTEXT}" -f "description=The merge gate could not run; re-run it"'
                ' \\\n  -f "target_url=${RUN_URL}"\n'
            ),
        },
    ],
}


def _workflow(name: str) -> dict[Any, Any]:
    """Return the workflow ``name``, parsed."""
    data: dict[Any, Any] = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    return data


def _triggers(workflow: dict[Any, Any]) -> dict[str, Any]:
    """Return the workflow's triggers (YAML 1.1 reads the key ``on`` as True)."""
    triggers: dict[str, Any] = workflow["on"] if "on" in workflow else workflow[True]
    return triggers


def test_the_gate_runs_only_the_default_branchs_devloop_with_no_secret() -> None:
    """The gate is called, never triggered; its one job is exactly the job written above."""
    workflow = _workflow("gate.yml")
    triggers = _triggers(workflow)
    assert set(triggers) == {"workflow_call"}
    assert set(triggers["workflow_call"]) == {"inputs"}
    inputs = triggers["workflow_call"]["inputs"]
    assert set(inputs) == {"devloop", "pr", "context"}
    # The name a broken run reports under, and part of the queue's name, so a second gate
    # beside another shares neither. It is "Merge gate" unless the caller says otherwise.
    assert inputs["context"] == {
        "description": inputs["context"]["description"],
        "required": False,
        "type": "string",
        "default": "Merge gate",
    }
    assert (inputs["devloop"]["required"], inputs["pr"]["required"]) == (True, False)
    assert inputs["pr"]["default"] == ""
    assert set(workflow) - {"on", True} == {"name", "permissions", "jobs"}
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"] == {"gate": GATE_JOB}
    assert "secrets" not in (WORKFLOWS / "gate.yml").read_text(encoding="utf-8")


def test_the_workflow_never_marks_a_head_pending_or_passed_itself() -> None:
    """Only devloop does, after it has seen that a status is due for this head.

    A pending written before that check would replace a branch's verdict when a fork's
    pull request has the same head commit.
    """
    text = (WORKFLOWS / "gate.yml").read_text(encoding="utf-8")
    assert "state=pending" not in text and "state=success" not in text
    assert text.count("state=") == 1 and text.count("statuses/") == 1
    writers = [step for step in GATE_JOB["steps"] if "statuses/" in step.get("run", "")]
    assert [step["name"] for step in writers] == ["Report a run that could not check"]
    assert writers[0]["if"].startswith("failure()")


def test_only_the_gate_workflow_posts_the_gates_status() -> None:
    """One writer: no other workflow names the status."""
    named = sorted(
        path.name
        for path in WORKFLOWS.glob("*.y*ml")
        if "Merge gate" in path.read_text(encoding="utf-8")
    )
    assert named == ["gate.yml"]


def test_the_model_reviews_run_the_gate_last() -> None:
    """After the labels check, whatever the reviews did, with no key."""
    workflow = _workflow("review-trusted.yml")
    assert list(workflow["jobs"])[-2:] == ["labels", "merge-gate"]
    assert workflow["jobs"]["merge-gate"] == {
        "needs": ["labels"],
        "if": "always()",
        "uses": GATE,
        "permissions": PERMISSIONS,
        "with": {"devloop": "."},
    }
    assert not [job for job in workflow["jobs"].values() if "merge-gate" in job.get("needs", [])]


def test_replies_run_the_gate_after_the_labels_check_but_not_for_a_fork() -> None:
    """A fork's review event has a read-only token: the gate could not write its status."""
    workflow = _workflow("labels-replies.yml")
    assert list(workflow["jobs"]) == ["labels", "merge-gate"]
    gate = workflow["jobs"]["merge-gate"]
    condition = " ".join(gate.pop("if").split())
    assert gate == {
        "needs": ["labels"],
        "uses": GATE,
        "permissions": PERMISSIONS,
        "with": {"devloop": "."},
    }
    assert condition == (
        "always() && (github.event_name != 'pull_request_review_comment'"
        " || github.event.sender.type != 'Bot')"
        " && github.event.pull_request.head.repo.full_name == github.repository"
    )
    # Every trigger of this workflow is a review event, so the fork rule covers them all.
    assert all(name.startswith("pull_request_review") for name in _triggers(workflow))


def test_dev_loops_own_decision_records_are_control_files() -> None:
    """A decision binds the code, so a change to one is never in the light lane here."""
    root = WORKFLOWS.parents[1]
    gate = rules.rules(load_config(root / "devloop.toml"))
    records = sorted(path.name for path in (root / "docs" / "decisions").glob("*.md"))
    assert len(records) >= 6
    for name in records:
        file = rules.ChangedFile(f"docs/decisions/{name}", "modified", None, "100644")
        found = rules.lane([file], expected=1, known=(), gate=gate)
        assert (found.light, found.control) == (False, True), name
    other = rules.ChangedFile("docs/gate.md", "modified", None, "100644")
    assert rules.lane([other], expected=1, known=(), gate=gate).light is True


def _find_head(tmp_path: Path, number: str, gh: str) -> tuple[int, str, str]:
    """Run the first step with a fake ``gh``; return its exit code, outputs and gh's arguments."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    asked, output = tmp_path / "asked", tmp_path / "output"
    asked.write_text("", encoding="utf-8")
    output.write_text("", encoding="utf-8")
    fake = bin_dir / "gh"
    fake.write_text(f'#!/bin/sh\necho "$@" >> "{asked}"\n{gh}\n', encoding="utf-8")
    fake.chmod(0o755)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "GITHUB_OUTPUT": str(output)}
    done = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", FIND_HEAD],
        env={**env, "GITHUB_REPOSITORY": "o/r", "PR_NUMBER": number},
        capture_output=True,
        check=False,
    )
    return done.returncode, output.read_text(), asked.read_text()


def test_the_head_is_read_fresh_from_the_api(tmp_path: Path) -> None:
    """The commit a failure is reported on is the pull request's head now."""
    sha = "0123456789abcdef" * 2 + "01234567"
    assert _find_head(tmp_path, "7", f"echo {sha}") == (
        0,
        f"sha={sha}\n",
        "api repos/o/r/pulls/7 --jq .head.sha\n",
    )


@pytest.mark.parametrize("number", ["", "7;id", "x", "-1", "7 8"])
def test_a_number_that_is_not_a_number_asks_github_nothing(tmp_path: Path, number: str) -> None:
    """The number reaches a URL, so it is checked first."""
    code, output, asked = _find_head(tmp_path, number, "echo " + "a" * 40)
    assert code == 1 and output == "" and asked == ""


@pytest.mark.parametrize("gh", ["exit 1", "echo", "echo null", "echo 'a b'", "echo ABC; exit 1"])
def test_a_head_that_cannot_be_read_is_a_failed_job_with_no_output(tmp_path: Path, gh: str) -> None:
    """No head, no status: the last step has nothing to report on."""
    code, output, _ = _find_head(tmp_path, "7", gh)
    assert code != 0 and output == ""

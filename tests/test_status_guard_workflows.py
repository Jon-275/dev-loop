"""The shared workflows write a status only after the status guard.

A status belongs to a commit: a fork's pull request whose head is also the head of a
branch in the base repository gets none (devloop.review.status_guard). The steps that
post are pinned whole here, so a status-writing line added to one fails a test, and the
shell guard of review.yml is run against a fake ``gh``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
RUN_URL = "${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
HEAD_SHA = "${{ github.event.pull_request.head.sha }}"
DUE = "steps.due.outputs.due == 'true'"
SHARED = (
    "::notice title=Status guard::No status written: this pull request comes from a fork "
    "and its head is also the head of a branch in this repository, whose status it would replace"
)
GUARD = {
    "name": "Check a status for this pull request may be written on its head",
    "id": "due",
    "env": {
        "GH_TOKEN": "${{ github.token }}",
        "HEAD_REPO": "${{ github.event.pull_request.head.repo.full_name }}",
        "HEAD_SHA": HEAD_SHA,
    },
    "run": (
        "due=true\n"
        'if [ "$HEAD_REPO" != "$GITHUB_REPOSITORY" ]; then\n'
        '  shared="$(gh api "repos/${GITHUB_REPOSITORY}/commits/${HEAD_SHA}/branches-where-head"'
        ' --jq length)"\n'
        '  case "$shared" in\n'
        "    0) ;;\n"
        "    '' | *[!0-9]*)\n"
        '      echo "::error title=Status guard::could not read the branches headed by this'
        ' commit"\n'
        "      exit 1\n"
        "      ;;\n"
        "    *)\n"
        "      due=false\n"
        f'      echo "{SHARED}"\n'
        "      ;;\n"
        "  esac\n"
        "fi\n"
        'echo "due=${due}" >> "$GITHUB_OUTPUT"\n'
    ),
}
WAITING = {
    "name": "Say the review waits for a maintainer",
    "if": f"steps.decide.outputs.reason == 'waiting' && {DUE}",
    "env": {
        "GH_TOKEN": "${{ github.token }}",
        "PROVIDER": "${{ inputs.provider }}",
        "APPROVAL_LABEL": "${{ inputs.approval_label }}",
        "HEAD_SHA": HEAD_SHA,
        "RUN_URL": RUN_URL,
    },
    "run": (
        'gh api "repos/${GITHUB_REPOSITORY}/statuses/${HEAD_SHA}" \\\n'
        '  -f state=pending -f "context=${PROVIDER^} review" \\\n'
        '  -f "description=Waiting for a maintainer to add the ${APPROVAL_LABEL} label" \\\n'
        '  -f "target_url=${RUN_URL}"\n'
    ),
}
PENDING = {
    "name": "Mark the review pending on the head commit",
    "if": DUE,
    "env": {
        "GH_TOKEN": "${{ github.token }}",
        "PROVIDER": "${{ inputs.provider }}",
        "HEAD_SHA": HEAD_SHA,
        "RUN_URL": RUN_URL,
    },
    "run": (
        'gh api "repos/${GITHUB_REPOSITORY}/statuses/${HEAD_SHA}" \\\n'
        '  -f state=pending -f "context=${PROVIDER^} review" \\\n'
        '  -f "description=Reviewing this commit" -f "target_url=${RUN_URL}"\n'
    ),
}
REPORT = {
    "name": "Report the review's outcome on the head commit",
    "if": "always() && steps.due.outputs.due != 'false'",
    "env": {
        "GH_TOKEN": "${{ github.token }}",
        "HEAD_SHA": HEAD_SHA,
        "PROVIDER": "${{ inputs.provider }}",
        "DUE": "${{ steps.due.outputs.due }}",
        "OUTCOME": "${{ steps.review.outcome }}",
        "DESCRIPTION": "${{ steps.review.outputs.status_description }}",
        "RUN_URL": RUN_URL,
    },
    "run": (
        'if [ "$OUTCOME" = "success" ] && [ "$DUE" = "true" ]; then\n'
        "  state=success\n"
        '  description="${DESCRIPTION:-Reviewed; resolve any finding threads before merge}"\n'
        'elif [ -n "$DESCRIPTION" ]; then\n'
        "  state=failure\n"
        '  description="$DESCRIPTION"\n'
        "else\n"
        "  state=failure\n"
        '  description="The review could not run; open the run for the reason"\n'
        "fi\n"
        'gh api "repos/${GITHUB_REPOSITORY}/statuses/${HEAD_SHA}" \\\n'
        '  -f "state=${state}" -f "context=${PROVIDER^} review" \\\n'
        '  -f "description=${description}" -f "target_url=${RUN_URL}"\n'
    ),
}
LABELS_STEPS: list[dict[str, Any]] = [
    {
        "name": "Check the pull request number and find its head",
        "id": "head",
        "run": (
            'case "$PR_NUMBER" in\n'
            "  ''|*[!0-9]*) echo \"::error title=Review labels::PR_NUMBER must be a number\";"
            " exit 1 ;;\n"
            "esac\n"
            'sha="$HEAD_SHA"\n'
            'if [ -z "$sha" ]; then\n'
            '  sha="$(gh api "repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}" --jq .head.sha)"\n'
            "fi\n"
            'echo "sha=${sha}" >> "$GITHUB_OUTPUT"\n'
        ),
    },
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
        "name": "Check every finding thread for a label",
        "id": "check",
        "timeout-minutes": 5,
        "run": "devloop labels",
    },
    {
        "name": "Report a run that posted no verdict",
        "if": (
            "failure() && steps.check.outputs.posted != 'true' && "
            "(steps.check.outputs.sha != '' || steps.head.outputs.sha != '')"
        ),
        "env": {"SHA": "${{ steps.check.outputs.sha || steps.head.outputs.sha }}"},
        "run": (
            'gh api "repos/${GITHUB_REPOSITORY}/statuses/${SHA}" -f state=failure '
            '-f "context=Review labels" '
            '-f "description=The labels check could not run; re-run it" '
            '-f "target_url=${RUN_URL}"'
        ),
    },
]


def _jobs(name: str) -> dict[str, Any]:
    """Return the jobs of the shared workflow ``name``."""
    jobs: dict[str, Any] = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))["jobs"]
    return jobs


def test_the_labels_job_is_pinned_and_its_shell_writes_only_the_failure_of_a_broken_run() -> None:
    """Pending and the verdict are devloop's, after its guard; the shell never posts them."""
    assert _jobs("labels.yml")["labels"]["steps"] == LABELS_STEPS
    writers = [s for s in LABELS_STEPS if "statuses/" in str(s.get("run", ""))]
    assert [s["name"] for s in writers] == ["Report a run that posted no verdict"]
    assert "state=failure" in str(writers[0]["run"]) and str(writers[0]["if"]).startswith(
        "failure()"
    )


def test_both_review_jobs_guard_with_the_same_step_before_they_post() -> None:
    """The gate's guard and the review's are one text, and each precedes its job's posts."""
    jobs = _jobs("review.yml")
    gate, review = jobs["gate"]["steps"], jobs["review"]["steps"]
    assert gate[1] == GUARD and review[0] == GUARD
    assert gate[2] == WAITING and review[1] == PENDING and review[-1] == REPORT
    assert len(gate) == 3


@pytest.mark.parametrize("name", ["review.yml", "council.yml", "labels.yml"])
def test_no_other_step_of_a_shared_workflow_writes_a_status(name: str) -> None:
    """Every shell step that names the statuses API is one pinned above."""
    pinned = [WAITING, PENDING, REPORT, LABELS_STEPS[-1]]
    for job in _jobs(name).values():
        for step in job["steps"]:
            run = step.get("run", "")
            assert ("statuses/" not in run and "state=" not in run) or step in pinned, step


def test_the_gate_reviews_nothing_when_the_status_is_not_due() -> None:
    """No review job, and so no key, for a pull request whose status cannot be written."""
    jobs = _jobs("review.yml")
    assert jobs["gate"]["outputs"]["run"] == (
        "${{ steps.decide.outputs.run == 'true' && steps.due.outputs.due == 'true' }}"
    )
    assert jobs["review"]["if"] == "needs.gate.outputs.run == 'true'"


def _guard(tmp_path: Path, head_repo: str, gh: str) -> tuple[int, str, str, str]:
    """Run the guard with a fake ``gh``; return its exit code, outputs, log and gh's arguments."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    asked, output = tmp_path / "asked", tmp_path / "output"
    asked.write_text("", encoding="utf-8")
    output.write_text("", encoding="utf-8")
    fake = bin_dir / "gh"
    fake.write_text(f'#!/bin/sh\necho "$@" >> "{asked}"\n{gh}\n', encoding="utf-8")
    fake.chmod(0o755)
    done = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", str(GUARD["run"])],
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "GITHUB_OUTPUT": str(output),
            "GITHUB_REPOSITORY": "o/r",
            "HEAD_REPO": head_repo,
            "HEAD_SHA": "a" * 40,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    return done.returncode, output.read_text(), done.stdout, asked.read_text()


BRANCHES = f"api repos/o/r/commits/{'a' * 40}/branches-where-head --jq length\n"


def test_the_shell_guard_asks_nothing_for_a_same_repository_pull_request(tmp_path: Path) -> None:
    assert _guard(tmp_path, "o/r", "echo 3") == (0, "due=true\n", "", "")


@pytest.mark.parametrize("head_repo", ["stranger/r", ""], ids=["fork", "deleted-fork"])
def test_the_shell_guard_judges_a_fork_by_the_branches_its_head_heads(
    tmp_path: Path, head_repo: str
) -> None:
    """Due when no branch here has that head; not due, with a notice, when one does."""
    assert _guard(tmp_path, head_repo, "echo 0") == (0, "due=true\n", "", BRANCHES)
    assert _guard(tmp_path, head_repo, "echo 2") == (0, "due=false\n", SHARED + "\n", BRANCHES)


@pytest.mark.parametrize("gh", ["exit 1", "echo", "echo '{}'", "echo 0; exit 1"])
def test_the_shell_guard_fails_when_it_could_not_check(tmp_path: Path, gh: str) -> None:
    """A read that fails, or an answer that is no count, is neither due nor not due."""
    code, output, _, asked = _guard(tmp_path, "stranger/r", gh)
    assert code != 0 and output == "" and asked == BRANCHES

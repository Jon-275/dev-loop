"""The shared workflows: pinned actions, least privilege, and the jobs main-health waits on."""

import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
FILES = sorted(WORKFLOWS.glob("*.yml"))
PINNED = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")


def _load(name: str) -> dict[Any, Any]:
    data: dict[Any, Any] = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    return data


def _on(data: dict[Any, Any]) -> dict[str, Any]:
    trigger: dict[str, Any] = data["on"] if "on" in data else data[True]  # YAML 1.1: on is True
    return trigger


def _steps(path: Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [step for job in data["jobs"].values() for step in job.get("steps", [])]


@pytest.mark.parametrize("path", FILES, ids=[p.name for p in FILES])
def test_every_action_is_pinned_by_commit_sha(path: Path) -> None:
    for step in _steps(path):
        if "uses" in step:
            assert PINNED.match(step["uses"]), step["uses"]


@pytest.mark.parametrize("path", FILES, ids=[p.name for p in FILES])
def test_no_expression_is_pasted_into_a_shell_command(path: Path) -> None:
    for step in _steps(path):
        assert "${{" not in step.get("run", ""), step["run"]


@pytest.mark.parametrize("path", FILES, ids=[p.name for p in FILES])
def test_checkouts_keep_no_credentials(path: Path) -> None:
    for step in _steps(path):
        if step.get("uses", "").startswith("actions/checkout@"):
            assert step["with"]["persist-credentials"] is False


@pytest.mark.parametrize("path", FILES, ids=[p.name for p in FILES])
def test_the_default_token_is_read_only_and_every_job_has_a_time_limit(path: Path) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["permissions"] == {"contents": "read"}
    for name, job in data["jobs"].items():
        assert "uses" in job or "timeout-minutes" in job, name


@pytest.mark.parametrize("name", ["pr-hygiene.yml", "main-health.yml"])
def test_the_shared_workflows_are_called_with_a_devloop_to_install(name: str) -> None:
    devloop = _on(_load(name))["workflow_call"]["inputs"]["devloop"]
    assert devloop["required"] is True and devloop["type"] == "string"


def test_pr_checks_read_the_merge_ref_and_its_base() -> None:
    jobs = _load("pr-hygiene.yml")["jobs"]
    for name in ("changes", "size", "changelog"):
        checkout = jobs[name]["steps"][0]
        assert checkout["with"]["fetch-depth"] == 2, name


def test_only_the_size_check_may_read_pull_requests() -> None:
    jobs = _load("pr-hygiene.yml")["jobs"]
    assert jobs["size"]["permissions"] == {"contents": "read", "pull-requests": "read"}
    assert [n for n, job in jobs.items() if "permissions" in job] == ["size"]
    assert jobs["size"]["steps"][-1]["env"] == {"GITHUB_TOKEN": "${{ github.token }}"}


def test_the_pr_only_checks_never_run_on_main() -> None:
    jobs = _load("pr-hygiene.yml")["jobs"]
    for name in ("size", "changelog"):
        assert "github.event_name == 'pull_request'" in jobs[name]["if"], name


def test_the_docs_only_answer_is_a_workflow_output() -> None:
    outputs = _on(_load("pr-hygiene.yml"))["workflow_call"]["outputs"]
    assert outputs["code"]["value"] == "${{ jobs.changes.outputs.code }}"
    assert outputs["doc_tests"]["value"] == "${{ jobs.changes.outputs.doc_tests }}"


def test_main_health_may_write_issues_and_nothing_else() -> None:
    job = _load("main-health.yml")["jobs"]["report"]
    assert job["permissions"] == {"contents": "read", "issues": "write"}
    assert job["steps"][-1]["env"]["NEEDS"] == "${{ inputs.needs }}"


def test_ci_reports_main_after_every_other_job_whatever_they_did() -> None:
    jobs = _load("ci.yml")["jobs"]
    health = jobs["main-health"]
    assert sorted(health["needs"]) == sorted(n for n in jobs if n != "main-health")
    assert "always()" in health["if"] and "github.event_name == 'push'" in health["if"]
    assert "github.ref == 'refs/heads/main'" in health["if"]
    assert health["with"]["needs"] == "${{ toJSON(needs) }}"
    assert health["permissions"] == {"contents": "read", "issues": "write"}


def test_ci_uses_its_own_shared_workflows_from_this_checkout() -> None:
    jobs = _load("ci.yml")["jobs"]
    assert jobs["hygiene"]["uses"] == "./.github/workflows/pr-hygiene.yml"
    assert jobs["hygiene"]["with"] == {"devloop": "."}
    assert jobs["main-health"]["uses"] == "./.github/workflows/main-health.yml"


def test_main_pushes_never_cancel_each_other() -> None:
    concurrency = _load("ci.yml")["concurrency"]
    assert "github.event_name == 'push' && github.sha" in concurrency["group"]
    # One group per PR for code runs and one for label changes, which never cancel each other.
    assert "github.event.action }}" not in concurrency["group"]
    assert "'labeled' || github.event.action == 'unlabeled') && '-label'" in concurrency["group"]
    assert concurrency["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"


def test_the_review_runs_only_trusted_code_and_reads_the_pull_request_as_data() -> None:
    steps = _load("review.yml")["jobs"]["review"]["steps"]
    checkouts = [s["with"] for s in steps if s.get("uses", "").startswith("actions/checkout@")]
    assert [(c["ref"], c["path"]) for c in checkouts] == [
        ("${{ github.sha }}", "trusted"),
        ("${{ github.event.pull_request.head.sha }}", "pr"),
    ]
    install = next(s for s in steps if s.get("name", "").startswith("Install devloop"))
    assert install["working-directory"] == "trusted"
    uv = next(s for s in steps if s.get("uses", "").startswith("astral-sh/setup-uv@"))
    assert uv["with"]["enable-cache"] is False and uv["with"]["version"] == "0.13.7"
    in_pr = [s["name"] for s in steps if s.get("working-directory") == "pr"]
    assert in_pr == ["Review"]
    assert '--trusted "$GITHUB_WORKSPACE/trusted"' in steps[-2]["run"]


def test_the_status_is_pending_right_after_the_guard_and_before_anything_else() -> None:
    guard, pending, *rest = _load("review.yml")["jobs"]["review"]["steps"]
    assert guard["id"] == "due" and "statuses/" not in guard["run"]
    assert "state=pending" in pending["run"] and "${PROVIDER^} review" in pending["run"]
    # Nothing runs, and no key is used, for a head whose status may not be written.
    assert [step["if"] for step in rest[:-1]] == ["steps.due.outputs.due == 'true'"] * 5


def test_only_the_review_step_holds_the_key() -> None:
    steps = _load("review.yml")["jobs"]["review"]["steps"]
    holding = [s["name"] for s in steps if "secrets." in str(s.get("env", {}))]
    assert holding == ["Review"]
    job = _load("review.yml")["jobs"]["review"]
    assert job["permissions"] == {"contents": "read", "pull-requests": "write", "statuses": "write"}
    assert job["timeout-minutes"] == 40


def test_the_status_always_reports_and_fails_closed() -> None:
    steps = _load("review.yml")["jobs"]["review"]["steps"]
    report = steps[-1]
    assert report["if"].startswith("always()")
    assert "state=failure" in report["run"] and report["env"]["OUTCOME"] == (
        "${{ steps.review.outcome }}"
    )


def test_dev_loop_reviews_itself_on_pull_request_target_from_the_base_branch() -> None:
    caller = _load("review-trusted.yml")
    assert list(_on(caller)) == ["pull_request_target"]
    for name in ("gemini", "reflection"):
        job = caller["jobs"][name]
        assert job["uses"] == "./.github/workflows/review.yml"
        assert job["with"] == {"provider": name, "devloop": "."}  # the default gate: members
    labels = caller["jobs"]["labels"]
    assert labels["uses"] == "./.github/workflows/labels.yml" and "always()" in labels["if"]
    assert sorted(labels["needs"]) == ["gemini", "reflection"]


def test_an_outside_pull_request_here_waits_for_a_maintainer_s_label() -> None:
    """Nobody outside spends the reviewer keys unasked: the repository is public."""
    caller = _load("review-trusted.yml")
    types = _on(caller)["pull_request_target"]["types"]
    assert "labeled" in types and "synchronize" in types
    keyed = [name for name, job in caller["jobs"].items() if "secrets" in job]
    assert sorted(keyed) == ["gemini", "reflection"]
    for name in keyed:
        assert "gate" not in caller["jobs"][name]["with"]  # so the default applies
    assert _on(_load("review.yml"))["workflow_call"]["inputs"]["gate"]["default"] == "members"
    assert caller["jobs"]["unapprove"] == {
        "uses": "./.github/workflows/unapprove.yml",
        "permissions": {**_load("unapprove.yml")["permissions"], "pull-requests": "write"},
    }
    # No keyed job waits for it: on a push the gate never reads the label, so an outside
    # pull request waits whether or not the label is still there.
    assert all("unapprove" not in job.get("needs", []) for job in caller["jobs"].values())
    decide = _load("review.yml")["jobs"]["gate"]["steps"][0]
    assert "labels" not in str(decide["env"]).lower().replace("approval_label", "")


UNAPPROVE = """case "$PR_NUMBER" in
  ''|*[!0-9]*) echo "::error title=Withdraw approval::no pull request number"; exit 1 ;;
esac
label="$(jq -rn --arg label "$APPROVAL_LABEL" '$label|@uri')"
url="repos/${GITHUB_REPOSITORY}/issues/${PR_NUMBER}/labels/${label}"
if ! out="$(gh api -X DELETE "$url" 2>&1)"; then
  case "$out" in
    *"HTTP 404"*) echo "The label was already removed." ;;
    *) echo "$out" >&2; exit 1 ;;
  esac
fi
"""


def test_a_push_withdraws_the_approval_without_a_key_or_a_checkout() -> None:
    """Approval covers one head, so the shared job removes the label when the head changes."""
    shared = _load("unapprove.yml")
    label = _on(shared)["workflow_call"]["inputs"]["approval_label"]
    review = _on(_load("review.yml"))["workflow_call"]["inputs"]["approval_label"]
    assert label["default"] == review["default"] == "ai-review-approved"
    assert shared["permissions"] == {"contents": "read"}
    assert shared["jobs"] == {
        "unapprove": {
            "name": "Withdraw approval on a new push",
            "if": "github.event.action == 'synchronize' && "
            "contains(github.event.pull_request.labels.*.name, inputs.approval_label)",
            "runs-on": "ubuntu-24.04",
            "timeout-minutes": 5,
            "permissions": {"pull-requests": "write"},
            "steps": [
                {
                    "name": "Remove the approval label",
                    "env": {
                        "GH_TOKEN": "${{ github.token }}",
                        "PR_NUMBER": "${{ github.event.pull_request.number }}",
                        "APPROVAL_LABEL": "${{ inputs.approval_label }}",
                    },
                    "run": UNAPPROVE,
                }
            ],
        }
    }


@pytest.mark.parametrize(
    ("code", "said", "expected"),
    [
        (0, "", 0),
        (1, "gh: Label does not exist (HTTP 404)", 0),
        (1, "gh: Resource not accessible by integration (HTTP 403)", 1),
        (1, "gh: Server Error (HTTP 502)", 1),
    ],
)
def test_removing_a_label_that_is_gone_succeeds_and_any_other_failure_fails(
    tmp_path: Path, code: int, said: str, expected: int
) -> None:
    """Two quick pushes both try; the second must not turn the workflow red."""
    fake = tmp_path / "gh"
    fake.write_text(f"#!/bin/sh\necho \"$*\" > '{tmp_path}/args'\necho '{said}' >&2\nexit {code}\n")
    fake.chmod(0o755)
    env = {
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        "GITHUB_REPOSITORY": "o/r",
        "PR_NUMBER": "7",
        "APPROVAL_LABEL": "needs review/ok",
    }
    done = subprocess.run(
        ["bash", "-e", "-c", UNAPPROVE], env=env, capture_output=True, check=False
    )
    assert done.returncode == expected, done.stderr
    assert "repos/o/r/issues/7/labels/needs%20review%2Fok" in (tmp_path / "args").read_text()


def test_the_label_job_refuses_an_event_with_no_pull_request(tmp_path: Path) -> None:
    """A caller on the wrong event gets an error, not a request for another path."""
    env = {"PATH": os.environ["PATH"], "GITHUB_REPOSITORY": "o/r", "PR_NUMBER": ""}
    done = subprocess.run(
        ["bash", "-e", "-c", UNAPPROVE], env=env, capture_output=True, check=False
    )
    assert done.returncode == 1 and b"no pull request number" in done.stdout


def test_the_labels_check_posts_from_devloop_and_reports_a_run_that_posted_nothing() -> None:
    steps = _load("labels.yml")["jobs"]["labels"]["steps"]
    # Pending is devloop's to post, after its guard (tests/test_status_guard_workflows.py
    # pins every step): the shell only checks the number and finds the head.
    assert "statuses/" not in steps[0]["run"] and "PR_NUMBER must be a number" in steps[0]["run"]
    checkout = steps[1]["with"]
    assert checkout["ref"] == "${{ github.event.repository.default_branch }}"
    assert steps[-1]["if"].startswith("failure() && steps.check.outputs.posted != 'true'")
    job = _load("labels.yml")["jobs"]["labels"]
    assert job["permissions"]["statuses"] == "write" and "secrets" not in str(job)
    # Runs overlap instead of cancelling each other, so no commit collects cancelled jobs.
    assert "concurrency" not in job
    # A bot's per-finding comment events are skipped (its review event runs the check).
    assert "github.event_name != 'pull_request_review_comment'" in job["if"]
    assert "github.event.sender.type != 'Bot'" in job["if"]


def test_replies_start_the_labels_check_without_the_model_keys() -> None:
    caller = _load("labels-replies.yml")
    assert sorted(_on(caller)) == ["pull_request_review", "pull_request_review_comment"]
    assert "secrets" not in str(caller) and "review.yml" not in str(caller)


def test_metrics_are_read_only_and_fail_when_devloop_fails() -> None:
    job = _load("metrics.yml")["jobs"]["metrics"]
    # Commit statuses tell finished review runs from failed ones; reading them needs
    # statuses: read, or GitHub refuses the whole query (FORBIDDEN).
    reads = {"contents": "read", "pull-requests": "read", "statuses": "read"}
    assert job["permissions"] == reads
    measure = next(s for s in job["steps"] if s.get("name") == "Measure the window")
    assert measure["run"].startswith("set -o pipefail")
    weekly = _load("metrics-weekly.yml")
    assert sorted(_on(weekly)) == ["schedule", "workflow_dispatch"]
    assert weekly["jobs"]["metrics"]["uses"] == "./.github/workflows/metrics.yml"
    assert weekly["jobs"]["metrics"]["permissions"] == reads


def test_the_council_status_runs_the_default_branch_s_code_with_no_secrets() -> None:
    """council.yml posts one status from the default branch's checkout and holds no key."""
    job = _load("council.yml")["jobs"]["council"]
    assert job["permissions"] == {"contents": "read", "pull-requests": "read", "statuses": "write"}
    assert "secrets" not in str(job)
    steps = job["steps"]
    assert steps[0]["with"]["ref"] == "${{ github.event.repository.default_branch }}"
    assert steps[0]["with"]["persist-credentials"] is False
    post = steps[-1]["run"]
    assert "devloop council report --post" in post
    assert '[ "$code" -le 1 ] || exit "$code"' in post

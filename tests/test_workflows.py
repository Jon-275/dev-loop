"""The shared workflows: pinned actions, least privilege, and the jobs main-health waits on."""

import re
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

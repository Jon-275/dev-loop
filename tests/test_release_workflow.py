"""The release workflow: when it runs, what it runs, and the one job that can write.

The job's token can create a tag and a release, so the workflow is pinned whole: an
added step, shell line, trigger or permission fails a test. The decision itself is
``devloop release-due`` (tests/test_release.py); the shell only acts on its answer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
NOTES = '"$RUNNER_TEMP/release-notes.md"'
JOB = {
    "name": "Tag and publish a new version",
    "runs-on": "ubuntu-24.04",
    "timeout-minutes": 10,
    "permissions": {"contents": "write"},
    "steps": [
        {
            "name": (
                "Check out this commit and its parent (the version is compared with the parent's)"
            ),
            "uses": "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            "with": {"fetch-depth": 2, "persist-credentials": False},
        },
        {
            "uses": "astral-sh/setup-uv@1db35dd8eee11c5894872e0b383ae6c406cf738d",
            "with": {"version": "0.13.7", "python-version": "3.12.14", "enable-cache": False},
        },
        {"name": "Install devloop from this commit", "run": "uv tool install ."},
        {
            "name": "Decide whether this commit is a release",
            "id": "due",
            "run": f"devloop release-due --output {NOTES}",
        },
        {
            "name": "Create the tag and publish the release",
            "if": "steps.due.outputs.release == 'true'",
            "env": {"GH_TOKEN": "${{ github.token }}", "TAG": "${{ steps.due.outputs.tag }}"},
            "run": (
                'gh release create "$TAG" --repo "$GITHUB_REPOSITORY" --target "$GITHUB_SHA" '
                f'--title "$TAG" --notes-file {NOTES}'
            ),
        },
    ],
}


def _load(name: str) -> dict[Any, Any]:
    data: dict[Any, Any] = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    return data


def test_the_release_workflow_is_exactly_this() -> None:
    """Runs on a push to main only, read-only by default, with the one job pinned whole."""
    workflow = _load("release.yml")
    # YAML 1.1 reads the key ``on`` as the boolean true.
    assert workflow == {
        "name": "Release",
        True: {"push": {"branches": ["main"]}},
        "permissions": {"contents": "read"},
        "jobs": {"release": JOB},
    }


def test_only_the_last_step_holds_the_token_and_it_waits_for_the_decision() -> None:
    steps = _load("release.yml")["jobs"]["release"]["steps"]
    assert [index for index, step in enumerate(steps) if "env" in step] == [len(steps) - 1]
    assert steps[-1]["if"] == "steps.due.outputs.release == 'true'"
    assert not any("continue-on-error" in step for step in steps)


def test_the_release_is_created_on_the_pushed_commit_with_no_flag_that_moves_a_tag() -> None:
    command = _load("release.yml")["jobs"]["release"]["steps"][-1]["run"].split()
    assert command[:4] == ["gh", "release", "create", '"$TAG"']
    assert command[command.index("--target") + 1] == '"$GITHUB_SHA"'
    flags = {word for word in command if word.startswith("-")}
    assert flags == {"--repo", "--target", "--title", "--notes-file"}


def test_no_other_workflow_can_write_contents_or_creates_a_release() -> None:
    for path in sorted(WORKFLOWS.glob("*.yml")):
        if path.name == "release.yml":
            continue
        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        for name, job in data["jobs"].items():
            assert job.get("permissions", {}).get("contents", "read") == "read", (path.name, name)
        assert "gh release" not in text and "git/refs" not in text and "git tag" not in text, path

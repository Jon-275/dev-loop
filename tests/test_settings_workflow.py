"""The settings check's workflow: what runs, with which token, and what it may write.

Invariants. The job is pinned whole here: an edit to a shell line, the environment or
any other key fails a test. The token that can read admin settings is used by the
read-only report and by nothing else; the issue is written with the workflow's own
token. The job may write issues and nothing else.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
PERMISSIONS = {"contents": "read", "issues": "write"}
COMPARE = (
    "set +e\n"
    'devloop doctor --repo "$GITHUB_REPOSITORY" > "$RUNNER_TEMP/settings-report.txt"\n'
    "code=$?\n"
    "set -e\n"
    'cat "$RUNNER_TEMP/settings-report.txt"\n'
    "# 0 clean, 1 a setting differs, 3 a setting was not judged; anything else broke.\n"
    'case "$code" in\n'
    '  0|1|3) echo "code=${code}" >> "$GITHUB_OUTPUT" ;;\n'
    '  *) exit "$code" ;;\n'
    "esac\n"
)
REPORT = (
    'number="$(gh issue list --state open --label "$LABEL" --limit 100 \\\n'
    "  --json number,title --jq 'map(select(.title == env.TITLE)) | .[0].number // empty')\"\n"
    'if [ "$CODE" = 0 ]; then\n'
    '  if [ -n "$number" ]; then\n'
    '    gh issue close "$number" --comment "The settings match devloop.toml again: ${RUN_URL}"\n'
    "  fi\n"
    "  exit 0\n"
    "fi\n"
    "{\n"
    "  echo 'A setting differs from `devloop.toml`, or could not be judged.'\n"
    "  echo 'This issue closes itself when a later run is clean.'\n"
    "  echo\n"
    "  echo '```'\n"
    '  cat "$RUNNER_TEMP/settings-report.txt"\n'
    "  echo '```'\n"
    "  echo\n"
    '  echo "Run: ${RUN_URL}"\n'
    '} > "$RUNNER_TEMP/settings-issue.md"\n'
    'if [ -n "$number" ]; then\n'
    '  gh issue edit "$number" --body-file "$RUNNER_TEMP/settings-issue.md"\n'
    "else\n"
    '  gh label create "$LABEL" --color FBCA04 --force \\\n'
    '    --description "the repository settings differ from devloop.toml"\n'
    '  gh issue create --title "$TITLE" --label "$LABEL" \\\n'
    '    --body-file "$RUNNER_TEMP/settings-issue.md"\n'
    "fi\n"
)
#: The whole job, word for word.
JOB: dict[str, Any] = {
    "name": "Check the repository settings",
    "runs-on": "ubuntu-24.04",
    "timeout-minutes": 5,
    "permissions": PERMISSIONS,
    "steps": [
        {
            "uses": "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            "with": {"persist-credentials": False},
        },
        {
            "uses": "astral-sh/setup-uv@1db35dd8eee11c5894872e0b383ae6c406cf738d",
            "with": {"version": "0.13.7", "python-version": "3.12.14"},
        },
        {
            "name": "Install devloop",
            "env": {"DEVLOOP": "${{ inputs.devloop }}"},
            "run": 'uv tool install "$DEVLOOP"',
        },
        {
            "name": "Compare the settings with devloop.toml",
            "id": "doctor",
            "env": {"GITHUB_TOKEN": "${{ secrets.token || github.token }}"},
            "run": COMPARE,
        },
        {
            "name": "Open, update or close the issue",
            "env": {
                "GH_TOKEN": "${{ github.token }}",
                "GH_REPO": "${{ github.repository }}",
                "CODE": "${{ steps.doctor.outputs.code }}",
                "TITLE": "Repository settings differ from devloop.toml",
                "LABEL": "repository-settings",
                "RUN_URL": (
                    "${{ github.server_url }}/${{ github.repository }}"
                    "/actions/runs/${{ github.run_id }}"
                ),
            },
            "run": REPORT,
        },
    ],
}


def _load(name: str) -> dict[Any, Any]:
    """Return the parsed workflow ``name``."""
    data: dict[Any, Any] = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    return data


def _on(data: dict[Any, Any]) -> dict[str, Any]:
    """Return the workflow's triggers (YAML 1.1 reads a bare ``on`` as true)."""
    trigger: dict[str, Any] = data["on"] if "on" in data else data[True]
    return trigger


def test_the_shared_workflow_is_pinned_whole() -> None:
    workflow = _load("settings-check.yml")
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"] == {"check": JOB}


def test_it_takes_a_devloop_to_install_and_an_optional_token() -> None:
    assert _on(_load("settings-check.yml")) == {
        "workflow_call": {
            "inputs": {
                "devloop": {
                    "description": (
                        "Where to install devloop from (a uv requirement, or . in dev-loop itself)"
                    ),
                    "required": True,
                    "type": "string",
                }
            },
            "secrets": {
                "token": {
                    "description": "A token that can read the repository's admin settings",
                    "required": False,
                }
            },
        }
    }


def test_the_admin_token_reaches_only_the_read_only_report() -> None:
    steps = JOB["steps"]
    holders = [step.get("name") for step in steps if "secrets." in str(step)]
    assert holders == ["Compare the settings with devloop.toml"]
    assert "gh " not in COMPARE and "devloop doctor --repo" in COMPARE
    assert "devloop" not in REPORT.replace("devloop.toml", "")


def test_no_expression_is_pasted_into_a_shell_line() -> None:
    for step in JOB["steps"]:
        assert "${{" not in step.get("run", "")


def test_the_actions_and_versions_are_those_of_the_other_shared_workflows() -> None:
    other = _load("main-health.yml")["jobs"]["report"]["steps"]
    assert [step for step in JOB["steps"] if "uses" in step] == other[:2]
    lines = (WORKFLOWS / "settings-check.yml").read_text(encoding="utf-8").splitlines()
    uses = [line.split("uses: ")[1] for line in lines if line.lstrip().startswith("- uses: ")]
    assert uses == [
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7",
        "astral-sh/setup-uv@1db35dd8eee11c5894872e0b383ae6c406cf738d # v10.4.0",
    ]


def test_dev_loop_calls_it_weekly_and_by_hand_with_the_default_token() -> None:
    caller = _load("settings-weekly.yml")
    assert _on(caller) == {"schedule": [{"cron": "45 6 * * 1"}], "workflow_dispatch": None}
    assert caller["permissions"] == {"contents": "read"}
    assert caller["jobs"] == {
        "settings": {
            "uses": "./.github/workflows/settings-check.yml",
            "permissions": PERMISSIONS,
            "with": {"devloop": "."},
        }
    }

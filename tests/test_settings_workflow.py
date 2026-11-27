"""The settings check's workflow: what runs, with which token, and what it may write.

States: no open issue, or one open issue titled TITLE with the label. Identity: the
title and the label together; a pull request or another title is never touched.

Invariants.

1. After any sequence of runs there is at most one open issue, and it is open exactly
   when the last report differed or could not be judged. Its body is the last report.
2. A clean run with no open issue writes nothing.
3. A ``gh`` call that fails fails the step: a lost write is never a green run.
4. One run at a time per repository, so two runs cannot both open an issue.
5. The job is pinned whole here: an edit to a shell line, the environment or any other
   key fails a test. The token that can read admin settings is used by the read-only
   report and by nothing else; the issue is written with the workflow's own token. The
   job may write issues and nothing else.

The issue step is run here, as the workflow has it, against a fake ``gh`` that keeps
issues in a file.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from workflow_retry import GH_API, ISSUE_CREATE

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
#: The issue step after the retry function, word for word.
REST = r"""issues="repos/${GITHUB_REPOSITORY}/issues"
open_issues() {  # every open issue with the label and exactly this title, oldest first
  gh_api -X GET "$issues" -f state=open -f labels="$LABEL" -f per_page=100 \
    --jq '[.[] | select(.pull_request == null and .title == env.TITLE) | .number] | sort | .[]'
}
numbers="$(open_issues)"
# shellcheck disable=SC2086  # the numbers are split into one argument each
set -- $numbers
if [ "$CODE" = 0 ]; then
  for number in "$@"; do
    gh_api "${issues}/${number}/comments" \
      -f body="The settings match devloop.toml again: ${RUN_URL}" > /dev/null
    gh_api -X PATCH "${issues}/${number}" -f state=closed > /dev/null
  done
  exit 0
fi
{
  echo 'A setting differs from `devloop.toml`, or could not be judged.'
  echo 'This issue closes itself when a later run is clean.'
  echo
  echo '```'
  cat "$RUNNER_TEMP/settings-report.txt"
  echo '```'
  echo
  echo "Run: ${RUN_URL}"
} > "$RUNNER_TEMP/settings-issue.md"
if [ "$#" -gt 0 ]; then
  gh_api -X PATCH "${issues}/$1" -F body=@"$RUNNER_TEMP/settings-issue.md" > /dev/null
  shift
  for extra in "$@"; do  # a second issue, from a run whose answer was lost: one is kept
    gh_api -X PATCH "${issues}/${extra}" -f state=closed -f state_reason=not_planned > /dev/null
  done
else
  gh_api "repos/${GITHUB_REPOSITORY}/labels/${LABEL}" > /dev/null 2>&1 ||
    gh_api "repos/${GITHUB_REPOSITORY}/labels" -f name="$LABEL" -f color=FBCA04 \
      -f description="the repository settings differ from devloop.toml" > /dev/null
  # Creating is not safe to send twice: GitHub may have made the issue and lost the
  # reply. So this call is not retried blindly: after a failure, look again, and
  # create only if the issue is still not there. Three tries at most.
  for try in 1 2 3; do
    if gh api "$issues" -f title="$TITLE" -f "labels[]=${LABEL}" \
      -F body=@"$RUNNER_TEMP/settings-issue.md" > /dev/null; then
      break
    fi
    if [ "$try" = 3 ]; then exit 1; fi
    sleep "$((try * 2))"
    numbers="$(open_issues)"
    if [ -n "$numbers" ]; then break; fi
  done
fi
"""
REPORT = GH_API + REST
#: The whole job, word for word.
JOB: dict[str, Any] = {
    "name": "Check the repository settings",
    "runs-on": "ubuntu-24.04",
    "timeout-minutes": 5,
    "permissions": PERMISSIONS,
    # One run at a time: two that both find no open issue would each open one.
    "concurrency": {
        "group": "settings-check-${{ github.repository }}",
        "cancel-in-progress": False,
    },
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
    assert "gh" not in COMPARE and "devloop doctor --repo" in COMPARE
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


TITLE = "Repository settings differ from devloop.toml"
LABEL = "repository-settings"
ISSUES = "repos/o/r/issues"
#: A fake ``gh api``: issues and labels live in ``$STATE``; ``$FAIL`` names a call to refuse.
FAKE_GH = r"""
import json, os, sys
args, state_file = sys.argv[1:], os.environ["STATE"]
state = json.load(open(state_file))
assert args[0] == "api", args
method, path, fields, skip = "", "", {}, 0
for index, arg in enumerate(args[1:], start=1):
    if skip:
        skip -= 1
    elif arg == "-X":
        method, skip = args[index + 1], 1
    elif arg in ("-f", "-F"):
        key, _, value = args[index + 1].partition("=")
        if arg == "-F" and value.startswith("@"):
            value = open(value[1:]).read()
        fields.setdefault(key, []).append(value)
        skip = 1
    elif arg == "--jq":
        fields["--jq"], skip = [args[index + 1]], 1
    else:
        path = arg
method = method or ("POST" if set(fields) - {"--jq"} else "GET")
state["calls"].append(f"{method} {path}")
json.dump(state, open(state_file, "w"))
if os.environ.get("FAIL") and os.environ["FAIL"] in f"{method} {path}":
    sys.exit("gh: Resource not accessible by integration (HTTP 403)")
# $ONCE is "lose:<call>" (done, but the reply is a 502) or "drop:<call>" (not done, a 502).
fault, _, target = os.environ.get("ONCE", "").partition(":")
fault = fault if target == f"{method} {path}" and not state.get("faulted") else ""
if fault:
    state["faulted"] = True
    json.dump(state, open(state_file, "w"))
if fault == "drop":
    sys.exit("gh: Server Error (HTTP 502)")
one = lambda key: fields[key][0]
parts = path.split("/")
if (method, path) == ("GET", "repos/o/r/issues"):
    assert (one("state"), one("labels")) == ("open", os.environ["LABEL"])
    for issue in sorted(state["issues"], key=lambda issue: issue["number"]):
        mine = one("labels") in issue["labels"] and issue["title"] == os.environ["TITLE"]
        if issue["state"] == "open" and mine and "pull_request" not in issue:
            print(issue["number"])
elif (method, path) == ("POST", "repos/o/r/issues"):
    number = 1 + max([issue["number"] for issue in state["issues"]], default=0)
    assert set(fields["labels[]"]) <= set(state["labels"]), "the label does not exist"
    new = {"number": number, "title": one("title"), "labels": fields["labels[]"]}
    state["issues"].append({**new, "state": "open", "body": one("body"), "comments": []})
elif method == "PATCH" and parts[:3] == ["repos", "o", "r"] and len(parts) == 5:
    issue = next(i for i in state["issues"] if i["number"] == int(parts[4]))
    issue.update({key: one(key) for key in ("state", "body") if key in fields})
elif method == "POST" and parts[5:] == ["comments"]:
    issue = next(i for i in state["issues"] if i["number"] == int(parts[4]))
    issue["comments"].append(one("body"))
elif method == "GET" and parts[3:4] == ["labels"]:
    if parts[4] not in state["labels"]:
        sys.exit("gh: Not Found (HTTP 404)")
elif (method, path) == ("POST", "repos/o/r/labels"):
    state["labels"].append(one("name"))
else:
    sys.exit(f"the fake gh has no answer for {method} {path}")
json.dump(state, open(state_file, "w"))
if fault == "lose":
    sys.exit("gh: Server Error (HTTP 502)")
"""


class Repo:
    """A repository's issues, as the fake ``gh`` keeps them, and the step run against them."""

    def __init__(self, tmp_path: Path, *issues: dict[str, Any]) -> None:
        """Write the fake ``gh`` and start with ``issues``."""
        self.tmp = tmp_path
        self.state = tmp_path / "state.json"
        self.state.write_text(
            json.dumps({"issues": list(issues), "labels": [LABEL], "calls": []}), encoding="utf-8"
        )
        (tmp_path / "bin").mkdir()
        (tmp_path / "fake_gh.py").write_text(FAKE_GH, encoding="utf-8")
        gh = tmp_path / "bin" / "gh"
        gh.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{tmp_path}/fake_gh.py" "$@"\n')
        gh.chmod(0o755)
        sleep = tmp_path / "bin" / "sleep"
        sleep.write_text(f"#!/bin/sh\necho \"$1\" >> '{tmp_path}/slept'\n", encoding="utf-8")
        sleep.chmod(0o755)

    def run(self, code: int, report: str = "differs    x", fail: str = "", once: str = "") -> int:
        """Run the issue step after a report that exited ``code``; return the step's exit code."""
        (self.tmp / "settings-report.txt").write_text(report + "\n", encoding="utf-8")
        env = {
            "PATH": f"{self.tmp / 'bin'}:/usr/bin:/bin",
            "GITHUB_REPOSITORY": "o/r",
            "RUNNER_TEMP": str(self.tmp),
            "STATE": str(self.state),
            "FAIL": fail,
            "ONCE": once,
            "CODE": str(code),
            "TITLE": TITLE,
            "LABEL": LABEL,
            "RUN_URL": "https://example.invalid/run",
        }
        script = ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", REPORT]
        return subprocess.run(script, env=env, capture_output=True, check=False).returncode

    def read(self, key: str) -> list[Any]:
        """Return ``issues``, ``labels`` or ``calls`` as they are now."""
        found: list[Any] = json.loads(self.state.read_text(encoding="utf-8"))[key]
        return found

    def open(self) -> list[dict[str, Any]]:
        """Return the open issues."""
        return [issue for issue in self.read("issues") if issue["state"] == "open"]

    def writes(self) -> list[str]:
        """Return the calls that were not reads."""
        return [call for call in self.read("calls") if not call.startswith("GET ")]


def _issue(number: int, title: str = TITLE, **more: object) -> dict[str, Any]:
    """Return one open issue with the label."""
    issue = {"number": number, "title": title, "labels": [LABEL], "state": "open"}
    return {**issue, "body": "old", "comments": [], **more}


def test_the_issue_follows_the_reports_open_updated_then_closed(tmp_path: Path) -> None:
    repo = Repo(tmp_path)
    repo.state.write_text(json.dumps({"issues": [], "labels": [], "calls": []}))
    assert repo.run(1, "differs    merge_methods") == 0
    (first,) = repo.open()
    assert (first["title"], first["labels"], repo.read("labels")) == (TITLE, [LABEL], [LABEL])
    assert "```\ndiffers    merge_methods\n```" in first["body"]
    assert "Run: https://example.invalid/run" in first["body"]
    assert repo.run(3, "not judged enforce_admins") == 0
    (second,) = repo.read("issues")
    assert second["number"] == first["number"] and second["state"] == "open"
    assert "not judged enforce_admins" in second["body"] and "merge_methods" not in second["body"]
    assert repo.run(0, "ok") == 0
    (closed,) = repo.read("issues")
    assert closed["state"] == "closed" and repo.open() == []
    assert closed["comments"] == [
        "The settings match devloop.toml again: https://example.invalid/run"
    ]
    assert repo.writes().count(f"POST {ISSUES}") == 1


def test_a_clean_report_with_no_open_issue_writes_nothing(tmp_path: Path) -> None:
    others = [_issue(1, "main is red: CI"), _issue(2, state="closed"), _issue(3, pull_request={})]
    repo = Repo(tmp_path, *others)
    assert repo.run(0, "ok") == 0
    assert repo.read("calls") == [f"GET {ISSUES}"]
    assert repo.read("issues") == others


@pytest.mark.parametrize(
    "codes", [(1, 1, 1), (0, 1, 0, 3), (3, 0, 0, 1), (1, 3, 1, 0), (0, 0), (3, 1, 0, 1, 3)]
)
def test_after_any_runs_one_issue_is_open_exactly_when_the_last_report_was_not_clean(
    tmp_path: Path, codes: tuple[int, ...]
) -> None:
    repo = Repo(tmp_path, _issue(1, "main is red: CI"))
    for turn, code in enumerate(codes):
        assert repo.run(code, f"report {turn}") == 0
        mine = [issue for issue in repo.open() if issue["title"] == TITLE]
        assert len(mine) == (0 if code == 0 else 1)
        assert all(f"report {turn}\n" in issue["body"] for issue in mine)
    assert [issue["number"] for issue in repo.open() if issue["title"] != TITLE] == [1]


def test_a_second_open_issue_is_closed_and_the_oldest_kept(tmp_path: Path) -> None:
    repo = Repo(tmp_path, _issue(9), _issue(4))
    assert repo.run(1, "differs    now") == 0
    assert [(issue["number"], "now" in issue["body"]) for issue in repo.open()] == [(4, True)]
    (tmp_path / "clean").mkdir()
    both = Repo(tmp_path / "clean", _issue(9), _issue(4))
    assert both.run(0, "ok") == 0 and both.open() == []


@pytest.mark.parametrize(
    ("code", "fail", "issues"),
    [
        (1, f"GET {ISSUES}", 0),
        (1, f"POST {ISSUES}", 0),
        (1, "POST repos/o/r/labels", 0),
        (1, f"PATCH {ISSUES}/1", 1),
        (0, f"GET {ISSUES}", 1),
        (0, f"POST {ISSUES}/1/comments", 1),
        (0, f"PATCH {ISSUES}/1", 1),
    ],
)
def test_a_gh_call_that_fails_fails_the_step(
    tmp_path: Path, code: int, fail: str, issues: int
) -> None:
    repo = Repo(tmp_path, *[_issue(1)][:issues])
    if "labels" in fail:
        repo.state.write_text(json.dumps({"issues": [], "labels": [], "calls": []}))
    assert repo.run(code, fail=fail) != 0
    assert any(call.startswith(fail) for call in repo.read("calls"))
    assert [issue["body"] for issue in repo.open()] == ["old"] * issues


def test_the_list_filter_keeps_only_this_issue_and_never_a_pull_request() -> None:
    jq = shutil.which("jq")
    if jq is None:
        pytest.skip("jq is not installed: the filter text is pinned, but was not run")
    expression = REST.split("--jq '")[1].split("'\n")[0]
    listed = [
        {"number": 8, "title": TITLE},
        {"number": 5, "title": TITLE, "pull_request": {"url": "x"}},
        {"number": 3, "title": TITLE + " "},
        {"number": 2, "title": TITLE},
    ]
    done = subprocess.run(
        [jq, expression],
        input=json.dumps(listed),
        env={"TITLE": TITLE},
        capture_output=True,
        text=True,
        check=True,
    )
    assert done.stdout.split() == ["2", "8"]


@pytest.mark.parametrize(("code", "passes"), [(0, True), (1, True), (3, True), (2, False)])
def test_the_report_step_passes_the_exit_code_on_and_fails_on_any_other(
    tmp_path: Path, code: int, passes: bool
) -> None:
    (tmp_path / "bin").mkdir()
    devloop = tmp_path / "bin" / "devloop"
    devloop.write_text(f'#!/bin/sh\necho "report for $3"\nexit {code}\n', encoding="utf-8")
    devloop.chmod(0o755)
    output = tmp_path / "output"
    output.write_text("", encoding="utf-8")
    env = {
        "PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin",
        "GITHUB_REPOSITORY": "o/r",
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_OUTPUT": str(output),
    }
    script = ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", COMPARE]
    done = subprocess.run(script, env=env, capture_output=True, text=True, check=False)
    assert done.stdout == "report for o/r\n"
    assert (done.returncode, output.read_text()) == ((0, f"code={code}\n") if passes else (2, ""))


def _fresh(tmp_path: Path) -> Repo:
    """Return a repository with no issue and no label yet."""
    repo = Repo(tmp_path)
    repo.state.write_text(json.dumps({"issues": [], "labels": [], "calls": []}))
    return repo


def test_a_create_whose_reply_is_lost_ends_with_exactly_one_open_issue(tmp_path: Path) -> None:
    """GitHub made the issue and answered 502: the step looks again and does not create twice."""
    repo = _fresh(tmp_path)
    assert repo.run(1, "differs    x", once=f"lose:POST {ISSUES}") == 0
    assert [issue["title"] for issue in repo.open()] == [TITLE]
    assert repo.read("calls").count(f"POST {ISSUES}") == 1
    assert repo.read("calls")[-1] == f"GET {ISSUES}"
    assert (tmp_path / "slept").read_text().split() == ["2"]
    assert repo.run(3, "not judged y") == 0 and len(repo.open()) == 1


def test_a_create_that_never_reached_github_is_sent_again_once(tmp_path: Path) -> None:
    repo = _fresh(tmp_path)
    assert repo.run(1, "differs    x", once=f"drop:POST {ISSUES}") == 0
    assert len(repo.open()) == 1 and repo.read("calls").count(f"POST {ISSUES}") == 2


def test_a_create_that_keeps_failing_fails_the_step_after_three_tries(tmp_path: Path) -> None:
    repo = _fresh(tmp_path)
    assert repo.run(1, "differs    x", fail=f"POST {ISSUES}") != 0
    assert repo.read("calls").count(f"POST {ISSUES}") == 3 and repo.open() == []
    assert (tmp_path / "slept").read_text().split() == ["2", "4"]


def test_only_the_create_is_sent_outside_the_retry_function() -> None:
    assert REST.count("gh api ") == 1 and ISSUE_CREATE in REST
    for call in ("PATCH", "/comments", "/labels", "-X GET"):
        assert all("gh_api " in line for line in REST.splitlines() if call in line), call

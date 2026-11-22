"""Tests for ``devloop main-health``: one "main is red" issue per workflow."""

import json
import subprocess

import pytest

from devloop import main_health as health
from devloop.cli import main

SHA = "a" * 40


class FakeGh:
    """Records gh calls; answers the head query and the issue list."""

    def __init__(self, open_issues: list[dict[str, object]], head: str = SHA) -> None:
        """Start with ``open_issues`` and main at ``head``."""
        self.open_issues = open_issues
        self.head = head
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> str:
        """Answer one gh command."""
        self.calls.append(args)
        if args[:1] == ["api"]:
            return self.head + "\n"
        if args[:2] == ["issue", "list"]:
            return json.dumps(self.open_issues)
        return ""

    def verbs(self) -> list[str]:
        """Return the gh commands run, other than the head query."""
        return [" ".join(call[:2]) for call in self.calls if call[:1] != ["api"]]


def _needs(**results: str) -> dict[str, dict[str, object]]:
    return {name: {"result": result, "outputs": {}} for name, result in results.items()}


@pytest.mark.parametrize("result", ["failure", "cancelled"])
def test_a_failed_or_timed_out_job_opens_the_issue(result: str) -> None:
    gh = FakeGh([])
    done = health.report(gh, "Checks", SHA, "url", _needs(quality=result, container="success"))
    assert done == "opened"
    assert gh.verbs() == ["issue list", "label create", "issue create"]
    create = gh.calls[-1]
    assert create[create.index("--title") + 1] == "main is red: Checks"
    assert f"`quality` {result}" in create[create.index("--body") + 1]


def test_a_second_red_run_comments_on_the_open_issue() -> None:
    gh = FakeGh([{"number": 7, "title": "main is red: Checks"}])
    assert health.report(gh, "Checks", SHA, "url", _needs(quality="failure")) == "commented"
    assert gh.calls[-1][:3] == ["issue", "comment", "7"]


def test_green_closes_only_this_workflows_issue() -> None:
    gh = FakeGh(
        [{"number": 3, "title": "main is red: CI"}, {"number": 7, "title": "main is red: Checks"}]
    )
    assert health.report(gh, "Checks", SHA, "url", _needs(quality="success")) == "closed"
    assert gh.calls[-1][:3] == ["issue", "close", "7"]


def test_green_with_no_open_issue_does_nothing() -> None:
    gh = FakeGh([])
    assert health.report(gh, "CI", SHA, "url", _needs(quality="success")) == "nothing to do"
    assert gh.verbs() == ["issue list"]


def test_a_skipped_job_is_not_red() -> None:
    assert health.red_jobs(_needs(quality="success", container="skipped")) == []


def test_a_run_for_an_older_commit_changes_nothing() -> None:
    gh = FakeGh([{"number": 7, "title": "main is red: Checks"}], head="b" * 40)
    done = health.report(gh, "Checks", SHA, "url", _needs(quality="success"))
    assert done.startswith("skipped: main has moved on to bbbbbbbbbbbb")
    assert gh.verbs() == []


def test_the_command_reports_what_it_did(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    gh = FakeGh([])
    monkeypatch.setattr(health, "run_gh", gh)
    needs = json.dumps(_needs(quality="success"))
    argv = ["main-health", "--workflow", "CI", "--sha", SHA, "--run-url", "u", "--needs", needs]
    assert main(argv) == 0
    assert capsys.readouterr().out == "CI at aaaaaaaaaaaa: nothing to do\n"


@pytest.mark.parametrize("needs", ["{}", "not json", "[]", '{"quality": "failure"}'])
def test_missing_job_results_are_a_configuration_error(
    needs: str, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["main-health", "--workflow", "CI", "--sha", SHA, "--run-url", "u", "--needs", needs]
    assert main(argv) == 2
    assert "needs" in capsys.readouterr().err


def test_a_failed_gh_call_is_one_line_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def failing(args: list[str]) -> str:
        """Fail like gh without permission."""
        raise subprocess.CalledProcessError(1, ["gh", *args], stderr="HTTP 403: Forbidden\n")

    monkeypatch.setattr(health, "run_gh", failing)
    needs = json.dumps(_needs(quality="failure"))
    argv = ["main-health", "--workflow", "CI", "--sha", SHA, "--run-url", "u", "--needs", needs]
    assert main(argv) == 2
    assert capsys.readouterr().err == "devloop: gh api failed: HTTP 403: Forbidden\n"

"""Tests for the benchmark runner and ``devloop bench`` (devloop.bench.runner).

The agent here is a script that makes the conforming fix, or does nothing, so the runner
is tested end to end (fixture, agent run, checks, scorecard) without a real agent.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from devloop.bench import runner
from devloop.bench.tasks import TASKS
from devloop.cli import main

GOOD_FIX = '''
import os, pathlib, subprocess, sys
root = pathlib.Path.cwd()
env = {**os.environ, "GIT_AUTHOR_NAME": "a", "GIT_AUTHOR_EMAIL": "a@example.invalid",
       "GIT_COMMITTER_NAME": "a", "GIT_COMMITTER_EMAIL": "a@example.invalid"}
assert "refunds" in sys.argv[1], sys.argv
core = root / "ledger" / "core.py"
core.write_text(core.read_text().replace(" if entry.cents > 0", ""))
test = root / "tests" / "test_ledger.py"
test.write_text(test.read_text().replace(
    '\\n\\nif __name__ == "__main__":',
    """
    def test_total_counts_refunds(self) -> None:
        \\"\\"\\"A refund lowers the total.\\"\\"\\"
        self.assertEqual(total([Entry("coffee", 350), Entry("refund", -100)]), 250)
\\n\\nif __name__ == "__main__":"""))
(root / "changes" / "refunds.fixed.md").write_text("- **Refunds count.**\\n")
print("agent transcript: secret-looking text that must stay in the log")
subprocess.run(["git", "checkout", "-q", "-b", "fix/refunds"], check=True, env=env)
subprocess.run(["git", "add", "-A"], check=True, env=env)
subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "fix: refunds"],
               check=True, env=env)
'''


def _agent(tmp_path: Path, code: str) -> runner.AgentCommand:
    """Return an agent command that runs ``code`` as a script with the prompt as argv[1]."""
    script = tmp_path / "agent.py"
    script.write_text(code, encoding="utf-8")
    return lambda prompt: [sys.executable, str(script), prompt]


def test_a_run_judges_the_agent_s_work_and_keeps_its_transcript_out_of_the_report(
    tmp_path: Path,
) -> None:
    """The fixture is set up, the agent runs in it, every check passes, the log is kept."""
    run = runner.run_task(TASKS["fix-bug"], "claude", _agent(tmp_path, GOOD_FIX), tmp_path / "out")
    assert run.agent_exit == 0
    assert all(result.passed for result in run.results), run.results
    assert "secret-looking text" in run.log.read_text(encoding="utf-8")
    report = runner.scorecard([run], "claude")
    assert "secret-looking text" not in report
    assert "6 of 6 checks passed" in report


def test_an_agent_that_does_nothing_fails_and_every_failure_is_named(tmp_path: Path) -> None:
    """Nothing changed: the scorecard counts the failures and names each with its reason."""
    run = runner.run_task(TASKS["fix-bug"], "claude", _agent(tmp_path, ""), tmp_path / "out")
    failed = [result for result in run.results if not result.passed]
    assert failed
    report = runner.scorecard([run], "claude")
    assert f"{len(run.results) - len(failed)} of {len(run.results)} checks passed" in report
    for result in failed:
        assert result.name in report and result.detail in report


def test_an_agent_that_runs_too_long_is_stopped_and_still_judged(tmp_path: Path) -> None:
    """A time limit stops the agent; what it left is judged, and the run says it timed out."""
    agent = _agent(tmp_path, "import time; time.sleep(30)")
    run = runner.run_task(TASKS["fix-bug"], "claude", agent, tmp_path / "out", timeout=1)
    assert run.agent_exit is None
    assert not all(result.passed for result in run.results)
    assert "timed out" in runner.scorecard([run], "claude")


def test_the_claude_command_is_headless_and_limited_to_the_task_s_tools() -> None:
    """Claude Code runs in print mode, without prompting, allowed only what the task needs."""
    argv = runner.claude_command("claude")("fix it")
    assert argv[:3] == ["claude", "-p", "fix it"]
    assert "--permission-mode" in argv
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    allowed = argv[argv.index("--allowedTools") + 1 :]
    assert "Bash(git *)" in allowed and "Bash(devloop *)" in allowed
    assert "--no-session-persistence" in argv and "--max-turns" in argv
    assert "--dangerously-skip-permissions" not in argv
    assert argv[argv.index("--permission-mode") + 1] != "bypassPermissions"
    assert not any(tool.startswith("WebFetch") for tool in allowed)


def test_bench_writes_the_scorecard_and_exits_by_its_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``devloop bench`` prints the scorecard, saves it as JSON and exits 1 on a failed check."""
    script = tmp_path / "agent.py"
    script.write_text("", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setitem(
        runner.AGENTS, "claude", lambda binary: lambda p: [sys.executable, binary, p]
    )
    out = tmp_path / "out"
    code = main(["bench", "--command", str(script), "--task", "fix-bug", "--out", str(out)])
    assert code == 1
    assert "checks passed" in capsys.readouterr().out
    saved = json.loads((out / "scorecard.json").read_text(encoding="utf-8"))
    assert saved["tool"] == "claude" and saved["tasks"][0]["task"] == "fix-bug"


def test_bench_without_the_agent_installed_says_how_to_install_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A missing agent CLI is exit 2 with the install step, not a row of failed checks."""
    missing = str(tmp_path / "no-such-claude")
    assert main(["bench", "--command", missing, "--out", str(tmp_path / "out")]) == 2
    assert "not found" in capsys.readouterr().err


def test_a_timed_out_agent_s_children_are_stopped_too(tmp_path: Path) -> None:
    """Nothing the agent started keeps changing the fixture after the time limit."""
    late = tmp_path / "late.txt"
    child = tmp_path / "child.py"
    child.write_text(f"import time; time.sleep(3); open({str(late)!r}, 'w').write('x')\n")
    code = (
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, {str(child)!r}])\n"
        "time.sleep(30)\n"
    )
    run = runner.run_task(TASKS["fix-bug"], "claude", _agent(tmp_path, code), tmp_path / "o", 1)
    assert run.agent_exit is None
    import time

    time.sleep(4)
    assert not late.exists()


def test_the_agent_sees_no_git_variables_of_the_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A GIT_DIR in the caller's shell never points the agent's git elsewhere."""
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "elsewhere"))
    code = "import os, sys; sys.exit(1 if any(k.startswith('GIT_') for k in os.environ) else 0)"
    run = runner.run_task(TASKS["fix-bug"], "claude", _agent(tmp_path, code), tmp_path / "o")
    assert run.agent_exit == 0


def test_runs_go_outside_the_project_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Agent logs never land in the working tree the command was run from."""
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    script = tmp_path / "agent.py"
    script.write_text("", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setitem(
        runner.AGENTS, "claude", lambda binary: lambda p: [sys.executable, binary, p]
    )
    main(["bench", "--command", str(script), "--task", "fix-bug", "--task", "fix-bug"])
    assert list(project.iterdir()) == []
    runs = list((tmp_path / "cache" / "devloop" / "bench").glob("*/fix-bug/agent.log"))
    assert len(runs) == 1
    assert str(runs[0].parents[1]) in capsys.readouterr().out

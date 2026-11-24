"""Run a coding agent on the benchmark's tasks and score the result: ``devloop bench``.

Each task gets a fresh copy of the fixture project. The agent runs headless in it with the
task's prompt, under a time limit; its transcript goes to a log file next to the
scorecard and is never printed, since it can hold anything the agent saw. Then the
task's checks judge what the agent left. Runs are local only (decision 0006): the agent
uses the maintainer's own login.

Exit codes: 0 every check passed, 1 a check failed, 2 the benchmark could not run (the
agent CLI is not installed, or an unknown task).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import checks
from .fixture import materialize
from .tasks import TASKS, Task

AgentCommand = Callable[[str], list[str]]

#: What the agent may do without asking: read and edit files, and run git, Python and
#: devloop. Nothing that reaches the network.
ALLOWED_TOOLS = (
    "Read",
    "Edit",
    "Write",
    "Glob",
    "Grep",
    "Bash(git *)",
    "Bash(python *)",
    "Bash(python3 *)",
    "Bash(devloop *)",
)
DEFAULT_TIMEOUT = 900
#: The most agentic turns one task may take.
MAX_TURNS = 50
INSTALL = {"claude": "install Claude Code: https://docs.anthropic.com/en/docs/claude-code"}


def claude_command(binary: str) -> AgentCommand:
    """Return how to run Claude Code headless on a prompt, allowed only ``ALLOWED_TOOLS``.

    In print mode a tool call that would prompt is denied: ``acceptEdits`` approves file
    edits, and each Bash command needs its allow rule, one argument per rule. The run
    uses the maintainer's own login, so their own settings and CLAUDE.md load too.
    """

    def argv(prompt: str) -> list[str]:
        return [
            binary,
            "-p",
            prompt,
            "--permission-mode",
            "acceptEdits",
            "--max-turns",
            str(MAX_TURNS),
            "--no-session-persistence",
            "--allowedTools",
            *ALLOWED_TOOLS,
        ]

    return argv


#: The agents the benchmark can run, by tool name: binary -> command.
AGENTS: dict[str, Callable[[str], AgentCommand]] = {"claude": claude_command}


@dataclass(frozen=True)
class Run:
    """One task's run: the checks' verdicts and how the agent ended."""

    task: str
    results: list[checks.Result]
    seconds: float
    #: The agent's exit code, or None when it was stopped at the time limit.
    agent_exit: int | None
    log: Path


def _env() -> dict[str, str]:
    """Return the agent's environment: this devloop and its Python first on ``PATH``."""
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), env.get("PATH", "")])
    env.pop("VIRTUAL_ENV", None)
    return env


def run_task(
    task: Task, tool: str, command: AgentCommand, out: Path, timeout: int = DEFAULT_TIMEOUT
) -> Run:
    """Run the agent on ``task`` in a fresh fixture under ``out`` and judge the result.

    Raises FileNotFoundError when the agent's binary does not exist.
    """
    work = out / task.name / "work"
    log = out / task.name / "agent.log"
    work.parent.mkdir(parents=True, exist_ok=True)
    base = materialize(work, tool)
    started = time.monotonic()
    agent_exit: int | None
    with log.open("w", encoding="utf-8") as sink:
        try:
            done = subprocess.run(
                command(task.prompt),
                cwd=work,
                env=_env(),
                stdin=subprocess.DEVNULL,
                stdout=sink,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
            )
            agent_exit = done.returncode
        except subprocess.TimeoutExpired:
            agent_exit = None
    seconds = time.monotonic() - started
    return Run(task.name, checks.run(task, work, base), seconds, agent_exit, log)


def _ending(run: Run) -> str:
    """Say how the agent's run ended."""
    if run.agent_exit is None:
        return f"timed out after {run.seconds:.0f}s"
    return f"exit {run.agent_exit}, {run.seconds:.0f}s"


def scorecard(runs: Sequence[Run], tool: str) -> str:
    """Return the Markdown scorecard: a row per task, then every failed check and why."""
    total = sum(len(run.results) for run in runs)
    passed = sum(result.passed for run in runs for result in run.results)
    lines = [
        f"## Conformance benchmark: {tool}",
        "",
        f"{passed} of {total} checks passed.",
        "",
        "| Task | Checks passed | Agent |",
        "|---|---|---|",
    ]
    for run in runs:
        ok = sum(result.passed for result in run.results)
        lines.append(f"| {run.task} | {ok} of {len(run.results)} | {_ending(run)} |")
    failed = [(run.task, r) for run in runs for r in run.results if not r.passed]
    if failed:
        lines += ["", f"Failed ({len(failed)}):", ""]
        lines += [f"- {task}: {result.name}: {result.detail}" for task, result in failed]
    return "\n".join(lines) + "\n"


def _save(runs: Sequence[Run], tool: str, out: Path) -> None:
    """Write the scorecard as Markdown and JSON next to the runs."""
    data = {
        "tool": tool,
        "tasks": [
            {
                "task": run.task,
                "agent_exit": run.agent_exit,
                "seconds": round(run.seconds, 1),
                "checks": [asdict(result) for result in run.results],
            }
            for run in runs
        ],
    }
    (out / "scorecard.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    (out / "scorecard.md").write_text(scorecard(runs, tool), encoding="utf-8")


def main(argv: Sequence[str]) -> int:
    """Run ``devloop bench``: every task, or those named, for one agent."""
    parser = argparse.ArgumentParser(prog="devloop bench", description=__doc__.split("\n")[0])
    parser.add_argument("--tool", choices=sorted(AGENTS), default="claude", help="the agent")
    parser.add_argument("--command", help="the agent's binary (default: the tool's name)")
    parser.add_argument("--task", action="append", help="a task to run (default: all)")
    parser.add_argument("--out", type=Path, help="where runs and the scorecard go")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="seconds per task")
    args = parser.parse_args(list(argv))
    names = args.task or list(TASKS)
    unknown = sorted(set(names) - set(TASKS))
    if unknown:
        print(f"devloop bench: unknown task(s): {', '.join(unknown)}", file=sys.stderr)
        return 2
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out: Path = (args.out or Path.cwd() / ".devloop" / "bench" / stamp).resolve()
    out.mkdir(parents=True, exist_ok=True)
    command = AGENTS[args.tool](args.command or args.tool)
    runs: list[Run] = []
    for name in names:
        try:
            runs.append(run_task(TASKS[name], args.tool, command, out, args.timeout))
        except FileNotFoundError as exc:
            hint = INSTALL.get(args.tool, "install it")
            print(f"devloop bench: {exc.filename} not found; {hint}", file=sys.stderr)
            return 2
    _save(runs, args.tool, out)
    print(scorecard(runs, args.tool), end="")
    print(f"Runs, agent logs and the scorecard: {out}")
    return 0 if all(r.passed for run in runs for r in run.results) else 1

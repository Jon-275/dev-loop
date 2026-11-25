"""The ``Agent: <tool>/<model>`` commit trailer: which coding agent wrote a commit.

An agent sets ``DEVLOOP_AGENT`` (for example ``claude-code/opus-5.5`` or ``codex/gpt-5``)
and git's ``commit-msg`` hook (``devloop agent --install``) adds the trailer to each commit
it writes. ``devloop metrics`` then reports every figure per authoring tool, so a tool that
drifts from the rules shows up as an outlier. The hook never blocks a commit: with no
``DEVLOOP_AGENT``, an invalid one, an existing trailer, or a project opt-out, it changes nothing.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from importlib import resources
from pathlib import Path

from devloop.config import CONFIG_FILE, ConfigError, load_config
from devloop.pr_open import PRE_COMMIT_MARKER

TRAILER = "Agent"
#: ``tool/model``: a tool name, a slash, and a model name (letters, digits, ``._:-``).
AGENT = re.compile(r"[a-z][a-z0-9-]{0,39}/[A-Za-z0-9][A-Za-z0-9._:-]{0,79}")
#: The trailer in a commit message: the last ``Agent:`` line of its trailer block.
_LINE = re.compile(rf"^{TRAILER}:[ \t]*(\S+)[ \t]*$", re.MULTILINE)
HOOK_MARKER = "# devloop commit-msg agent trailer"
Runner = Callable[..., subprocess.CompletedProcess[str]]


def agent_of(message: str) -> str | None:
    """Return the tool of a commit message's ``Agent:`` trailer (``codex``), or ``None``.

    Only the message's last paragraph is read, where git keeps trailers, and only a
    valid ``tool/model`` value counts.
    """
    last = message.strip().split("\n\n")[-1] if message.strip() else ""
    found = [m.group(1) for m in _LINE.finditer(last) if AGENT.fullmatch(m.group(1))]
    return found[-1].split("/", 1)[0] if found else None


def add_trailer(path: Path, agent: str, run: Runner = subprocess.run) -> bool:
    """Add ``Agent: agent`` to the commit message at ``path``; return whether it was added.

    Nothing is added for an invalid value or a message with any ``Agent:`` trailer, valid
    or not; the result says whether the file really changed.
    """
    before = path.read_text(encoding="utf-8")
    if not AGENT.fullmatch(agent) or _LINE.search(before.strip().split("\n\n")[-1]):
        return False
    done = run(
        [
            "git",
            "interpret-trailers",
            "--in-place",
            "--if-exists",
            "doNothing",
            "--trailer",
            f"{TRAILER}: {agent}",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return done.returncode == 0 and path.read_text(encoding="utf-8") != before


def install(run: Runner = subprocess.run) -> int:
    """Install the trailer hook as this clone's ``commit-msg`` hook; 2 when it cannot.

    A ``commit-msg`` hook written by pre-commit keeps its place and this one becomes
    ``commit-msg.legacy``, which pre-commit runs first; any other hook is never replaced.
    """
    found = run(
        ["git", "rev-parse", "--path-format=absolute", "--git-path", "hooks"],
        capture_output=True,
        text=True,
        check=False,
    )
    if found.returncode != 0 or not found.stdout.strip():
        print("error: not in a git repository", file=sys.stderr)
        return 2
    hooks = Path(found.stdout.strip())
    hooks.mkdir(parents=True, exist_ok=True)
    target = hooks / "commit-msg"
    if target.exists():
        text = target.read_text(encoding="utf-8", errors="replace")
        if PRE_COMMIT_MARKER in text:
            target = hooks / "commit-msg.legacy"  # pre-commit runs it first
            text = target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""
        if text and HOOK_MARKER not in text:
            print(
                f"error: {target} is another commit-msg hook; not replacing it. Call "
                '`devloop agent --commit-msg "$1"` from it.',
                file=sys.stderr,
            )
            return 2
    hook = resources.files("devloop").joinpath("templates", "hooks", "commit-msg")
    target.write_text(hook.read_text(encoding="utf-8"), encoding="utf-8")
    target.chmod(0o755)
    print(f"installed the Agent trailer hook at {target}")
    return 0


def main(argv: Sequence[str], env: Mapping[str, str] | None = None) -> int:
    """Run ``devloop agent``: add the trailer to a message, or install the hook."""
    parser = argparse.ArgumentParser(prog="devloop agent", description=__doc__)
    parser.add_argument("--commit-msg", type=Path, help="the commit message file git passes")
    parser.add_argument("--install", action="store_true", help="install the commit-msg hook")
    args = parser.parse_args(list(argv))
    if args.install:
        return install()
    if args.commit_msg is None:
        parser.error("give --commit-msg FILE or --install")
    agent = (os.environ if env is None else env).get("DEVLOOP_AGENT", "").strip()
    if agent and not AGENT.fullmatch(agent):
        print(
            f"devloop agent: DEVLOOP_AGENT={agent!r} is not tool/model; no trailer added",
            file=sys.stderr,
        )
    elif agent:
        try:
            config = Path(CONFIG_FILE)
            if not config.is_file() or load_config(config).agents.commit_attribution:
                add_trailer(args.commit_msg, agent)
        except (ConfigError, OSError, UnicodeError, subprocess.SubprocessError) as error:
            print(f"devloop agent: no trailer added ({type(error).__name__})", file=sys.stderr)
    return 0  # the hook never blocks a commit

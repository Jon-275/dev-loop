"""``devloop doctor``: check a project is set up and nothing has drifted from its sources."""

from __future__ import annotations

from pathlib import Path

from devloop import __version__
from devloop.agents_md import AGENTS_FILE, BlockError, missing_sections, parse_blocks
from devloop.config import CONFIG_FILE, Config, ConfigError, load_config
from devloop.council import problems as council_problems
from devloop.sync import CLAUDE_FILE, CLAUDE_IMPORT, GEMINI_SETTINGS, SyncError, plan
from devloop.tools import TOOLS


def _version_problem(config: Config) -> str | None:
    """Report a config pinned to a framework version other than the installed one."""
    installed = ".".join(__version__.split(".")[:2])
    if config.framework.version != installed:
        return (
            f"{CONFIG_FILE}: framework.version is {config.framework.version} but devloop "
            f"{installed} is installed; install that version or upgrade the project"
        )
    return None


def _agents_md_problems(root: Path) -> list[str]:
    """Report a missing AGENTS.md, malformed markers or missing project sections."""
    path = root / AGENTS_FILE
    if not path.is_file():
        return [f"{AGENTS_FILE} is missing; run `devloop sync`"]
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return [f"{AGENTS_FILE} is not UTF-8 text"]
    try:
        parse_blocks(text)
    except BlockError as exc:
        return [f"{AGENTS_FILE}: {exc}"]
    return [f"{AGENTS_FILE}: required section {s!r} is missing" for s in missing_sections(text)]


def _tool_problems(config: Config) -> list[str]:
    """Report a configured tool that no rendered skills folder serves."""
    problems = []
    for name in config.agents.tools:
        tool = TOOLS[name]
        if not set(tool.skills_dirs) & set(config.agents.skills_targets):
            problems.append(
                f"{tool.title} reads skills only from {', '.join(tool.skills_dirs)}; "
                "add one to agents.skills_targets"
            )
    return problems


def _sync_problems(root: Path, config: Config) -> list[str]:
    """Report every file whose rendered content differs from its source."""
    try:
        changes = plan(root, config)
    except SyncError as exc:
        return [str(exc)]
    labels = {
        CLAUDE_FILE: f"{CLAUDE_FILE} does not import {CLAUDE_IMPORT}",
        GEMINI_SETTINGS: f"{GEMINI_SETTINGS} does not load {AGENTS_FILE}",
    }
    return [
        f"drift: {c.path} would be {c.action}d by `devloop sync`"
        + (f" ({labels[c.path]})" if c.path in labels else "")
        for c in changes
    ]


def diagnose(root: Path) -> list[str]:
    """Return every problem found in the project at ``root``; empty means healthy."""
    try:
        config = load_config(root / CONFIG_FILE)
    except ConfigError as exc:
        return [str(exc)]
    problems = [p for p in [_version_problem(config)] if p]
    problems += _agents_md_problems(root)
    problems += _tool_problems(config)
    problems += _sync_problems(root, config)
    problems += council_problems(root, config)
    return problems

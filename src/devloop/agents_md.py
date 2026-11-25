"""AGENTS.md managed blocks: rendered from the template, rewritten on sync, never guessed.

A managed block is the text between ``<!-- devloop:begin NAME -->`` and
``<!-- devloop:end NAME -->`` lines. Everything outside the blocks belongs to the project.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from string import Template

from devloop.config import Config

AGENTS_FILE = "AGENTS.md"
REQUIRED_SECTIONS = ("## About this project", "## Commands")
_MARKER = re.compile(r"^<!-- devloop:(begin|end) ([a-z][a-z0-9-]*) -->$")
_ATTRIBUTION_RULE = (
    "- Set `DEVLOOP_AGENT=<tool>/<model>` (for example `codex/gpt-5`) in the environment you\n"
    "  commit from, once per clone run `devloop agent --install`, and every commit then carries\n"
    "  an `Agent:` trailer, so the weekly metrics report each tool's figures."
)
_REPOSITORY_IDENTITY_RULE = (
    "- Use the normal repository identity for commits.\n"
    "  Keep coding-tool attribution out of commit metadata, messages and pull request descriptions."
)


class BlockError(ValueError):
    """Raised when managed-block markers are unbalanced, nested or repeated."""


@dataclass(frozen=True)
class _Block:
    """A managed block's name and the line span of its markers (inclusive)."""

    name: str
    start: int
    end: int


def _scan(lines: list[str]) -> list[_Block]:
    """Find every managed block in ``lines``, raising on malformed markers.

    Markers inside fenced code blocks are examples, not blocks, and are ignored.
    """
    blocks: list[_Block] = []
    open_name: str | None = None
    open_at = 0
    fence: str | None = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if fence is None and stripped[:3] in {"```", "~~~"}:
            fence = stripped[:3]
            continue
        if fence is not None:
            if stripped.startswith(fence):
                fence = None
            continue
        match = _MARKER.match(stripped)
        if not match:
            continue
        kind, name = match.groups()
        if kind == "begin":
            if open_name is not None:
                raise BlockError(f"block {name!r} begins inside block {open_name!r}")
            if any(b.name == name for b in blocks):
                raise BlockError(f"block {name!r} appears twice")
            open_name, open_at = name, i
        elif open_name is None:
            raise BlockError(f"end of block {name!r} without a begin (line {i + 1})")
        elif name != open_name:
            raise BlockError(f"end marker for {name!r} closes block {open_name!r}")
        else:
            blocks.append(_Block(name, open_at, i))
            open_name = None
    if open_name is not None:
        raise BlockError(f"block {open_name!r} is never closed")
    return blocks


def parse_blocks(text: str) -> dict[str, str]:
    """Return each managed block's body, keyed by block name."""
    lines = text.splitlines(keepends=True)
    return {b.name: "".join(lines[b.start + 1 : b.end]) for b in _scan(lines)}


def _wrap(name: str, body: str) -> str:
    """Return a managed block with its markers; the body always ends with a newline."""
    if body and not body.endswith("\n"):
        body += "\n"
    return f"<!-- devloop:begin {name} -->\n{body}<!-- devloop:end {name} -->\n"


def apply_blocks(text: str, blocks: Mapping[str, str]) -> str:
    """Rewrite ``text`` so its managed blocks are exactly ``blocks``.

    Existing blocks get the new body in place, blocks the framework retired are removed,
    and new ones are appended. Text outside the blocks is returned unchanged.
    """
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    pos = 0
    for block in _scan(lines):
        out.extend(lines[pos : block.start])
        if block.name in blocks:
            out.append(_wrap(block.name, blocks[block.name]))
        pos = block.end + 1
    out.extend(lines[pos:])
    result = "".join(out)
    present = {b.name for b in _scan(lines)}
    for name, body in blocks.items():
        if name not in present:
            if result and not result.endswith("\n"):
                result += "\n"
            result += ("\n" if result else "") + _wrap(name, body)
    return result


def render_template(config: Config) -> tuple[dict[str, str], str]:
    """Render the AGENTS.md template for ``config``.

    Returns the managed blocks and the whole starter document (blocks plus the project
    sections a new project fills in).
    """
    raw = resources.files("devloop").joinpath("templates", AGENTS_FILE).read_text("utf-8")
    labels = " or ".join(f"`{p}:`" for p in config.labels.prefixes)
    doc = Template(raw).substitute(
        max_lines=config.pr.max_lines,
        large_label=config.pr.large_label,
        labels=labels,
        commit_attribution=(
            _ATTRIBUTION_RULE if config.agents.commit_attribution else _REPOSITORY_IDENTITY_RULE
        ),
    )
    return parse_blocks(doc), doc


def missing_sections(text: str) -> list[str]:
    """Return the required project headings that ``text`` lacks."""
    headings = {line.strip() for line in text.splitlines()}
    return [s for s in REQUIRED_SECTIONS if s not in headings]

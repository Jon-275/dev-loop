"""Load skills and enforce the portable Agent Skills frontmatter (``name``, ``description``).

Only the two fields every supported tool reads are allowed, with the agentskills.io rules
for ``name`` (1-64 lower-case letters, digits and single hyphens, matching the folder).
Copilot in VS Code silently skips a skill whose name breaks them, so they are errors here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

SKILL_FILE = "SKILL.md"
PORTABLE_FIELDS = ("name", "description")
_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_FIELD = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):(.*)$")


class SkillError(ValueError):
    """Raised when a skill folder is missing SKILL.md or its frontmatter is not portable."""


@dataclass(frozen=True)
class Skill:
    """A skill: its validated name and description, and every file in its folder."""

    name: str
    description: str
    files: dict[str, bytes]


def parse_frontmatter(text: str) -> dict[str, str]:
    """Parse single-line ``key: value`` frontmatter between the leading ``---`` lines."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillError("SKILL.md must start with --- frontmatter")
    meta: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return meta
        match = _FIELD.match(line)
        if not match:
            raise SkillError(f"frontmatter values must be on a single line: {line!r}")
        key, value = match.group(1), match.group(2).strip()
        if value in {">", "|", ">-", "|-", ""}:
            raise SkillError(f"frontmatter {key!r} must be a single line")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif ": " in value or " #" in value or value[0] in "!&*[]{}|>%@`\"'":
            raise SkillError(f"frontmatter {key!r}: quote a value holding YAML syntax")
        if key in meta:
            raise SkillError(f"frontmatter {key!r} appears twice")
        meta[key] = value
    raise SkillError("SKILL.md frontmatter is never closed with ---")


def _validate(meta: dict[str, str], folder: str) -> tuple[str, str]:
    """Check ``meta`` against the portable rules; return ``(name, description)``."""
    extra = sorted(set(meta) - set(PORTABLE_FIELDS))
    if extra:
        raise SkillError(f"{folder}: {extra} not portable; only {PORTABLE_FIELDS} are allowed")
    name, description = meta.get("name"), meta.get("description")
    if not name:
        raise SkillError(f"{folder}: frontmatter needs a name")
    if not description:
        raise SkillError(f"{folder}: frontmatter needs a description")
    if len(name) > 64:
        raise SkillError(f"{folder}: name is longer than 64 characters")
    if not _NAME.match(name):
        raise SkillError(f"{folder}: name must be lower-case letters, digits and single hyphens")
    if name != folder:
        raise SkillError(f"{folder}: name {name!r} must match its folder")
    if len(description) > 1024:
        raise SkillError(f"{folder}: description is longer than 1024 characters")
    return name, description


def _files(root: Traversable, prefix: str = "") -> dict[str, bytes]:
    """Return every file under ``root`` keyed by its POSIX path relative to ``root``."""
    files: dict[str, bytes] = {}
    for entry in sorted(root.iterdir(), key=lambda e: e.name):
        if entry.name == "__pycache__":
            continue
        rel = f"{prefix}{entry.name}"
        if entry.is_dir():
            files.update(_files(entry, f"{rel}/"))
        else:
            files[rel] = entry.read_bytes()
    return files


def load_skill(folder: Traversable | Path) -> Skill:
    """Load and validate the skill in ``folder``."""
    skill_md = folder / SKILL_FILE
    if not skill_md.is_file():
        raise SkillError(f"{folder.name}: no {SKILL_FILE}")
    meta = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
    name, description = _validate(meta, folder.name)
    return Skill(name=name, description=description, files=_files(folder))


def load_skills(root: Traversable | Path) -> dict[str, Skill]:
    """Load every skill folder directly under ``root`` (none if it does not exist)."""
    if not root.is_dir():
        return {}
    return {
        entry.name: load_skill(entry)
        for entry in sorted(root.iterdir(), key=lambda e: e.name)
        if entry.is_dir() and entry.name != "__pycache__"
    }


def framework_skills() -> dict[str, Skill]:
    """Return the skills shipped with devloop."""
    return load_skills(resources.files("devloop").joinpath("skills"))

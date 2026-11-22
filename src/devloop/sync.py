"""``devloop sync``: render skills and adapters from one source into every tool's location.

The desired state is computed in full before anything is written, so an error leaves the
project untouched. Rendered skill files are listed in a manifest; only listed files are
ever replaced or deleted, so a skill the project keeps by hand in a target is never lost.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from devloop.agents_md import AGENTS_FILE, BlockError, apply_blocks, render_template
from devloop.config import Config
from devloop.skills import Skill, SkillError, framework_skills, load_skills

MANIFEST = ".devloop/manifest.json"
CLAUDE_FILE = "CLAUDE.md"
CLAUDE_IMPORT = "@AGENTS.md"
GEMINI_SETTINGS = ".gemini/settings.json"

Action = Literal["create", "update", "delete"]


class SyncError(ValueError):
    """Raised when the sources are invalid or a sync would overwrite unmanaged files."""


@dataclass(frozen=True)
class Change:
    """One file that sync creates, rewrites or deletes."""

    path: str
    action: Action


def _read(root: Path, rel: str) -> bytes | None:
    """Return the bytes of ``root/rel``, or None if it does not exist."""
    path = root / rel
    return path.read_bytes() if path.is_file() else None


def _skills(root: Path, config: Config) -> dict[str, Skill]:
    """Return the framework skills the config includes plus the project's own skills."""
    project_dir = PurePosixPath(config.skills.project)
    for target in config.agents.skills_targets:
        tpath = PurePosixPath(target)
        if project_dir == tpath or tpath in project_dir.parents or project_dir in tpath.parents:
            raise SyncError(f"skills.project {project_dir} overlaps skills target {target}")
    try:
        shipped = framework_skills()
        project = load_skills(root / config.skills.project)
    except SkillError as exc:
        raise SyncError(str(exc)) from exc
    chosen: dict[str, Skill] = {}
    for name in config.skills.include:
        if name not in shipped:
            raise SyncError(f"skills.include: unknown framework skill {name!r}")
        chosen[name] = shipped[name]
    for name, skill in project.items():
        if name in shipped:
            raise SyncError(f"skill {name!r} is both a framework and a project skill; rename it")
        chosen[name] = skill
    return chosen


def _claude_md(current: bytes | None) -> bytes:
    """Return CLAUDE.md with the ``@AGENTS.md`` import, keeping any existing text."""
    if current is None:
        return f"{CLAUDE_IMPORT}\n".encode()
    text = current.decode("utf-8")
    if any(line.strip() == CLAUDE_IMPORT for line in text.splitlines()):
        return current
    return f"{CLAUDE_IMPORT}\n\n{text}".encode()


def _gemini_settings(current: bytes | None) -> bytes:
    """Return Gemini CLI settings whose ``context.fileName`` loads AGENTS.md.

    Other settings are kept, and a file that already loads AGENTS.md is returned as is.
    """
    try:
        data = json.loads(current) if current is not None else {}
    except json.JSONDecodeError as exc:
        raise SyncError(f"{GEMINI_SETTINGS} is not valid JSON: {exc}") from exc
    context = data.get("context", {}) if isinstance(data, dict) else None
    if not isinstance(data, dict) or not isinstance(context, dict):
        raise SyncError(f"{GEMINI_SETTINGS} must be an object with an object 'context'")
    names = context.get("fileName", ["GEMINI.md"])
    names = [names] if isinstance(names, str) else names
    if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
        raise SyncError(f"{GEMINI_SETTINGS}: context.fileName must be a string or list")
    if AGENTS_FILE in names and current is not None:
        return current
    if AGENTS_FILE not in names:
        names = [AGENTS_FILE, *names]
    data["context"] = {**context, "fileName": names}
    return (json.dumps(data, indent=2) + "\n").encode()


def _agents_md(root: Path, config: Config) -> bytes:
    """Return AGENTS.md with current managed blocks (the starter document if absent)."""
    blocks, starter = render_template(config)
    current = _read(root, AGENTS_FILE)
    if current is None:
        return starter.encode()
    try:
        return apply_blocks(current.decode("utf-8"), blocks).encode()
    except BlockError as exc:
        raise SyncError(f"{AGENTS_FILE}: {exc}") from exc


def _manifest(root: Path) -> set[str]:
    """Return the rendered skill files the previous sync recorded."""
    current = _read(root, MANIFEST)
    if current is None:
        return set()
    try:
        paths = json.loads(current)["files"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise SyncError(f"{MANIFEST} is unreadable; delete it and sync again: {exc}") from exc
    return set(paths)


def desired(root: Path, config: Config) -> tuple[dict[str, bytes], set[str]]:
    """Return every file sync owns with its content, and the paths to delete."""
    files: dict[str, bytes] = {AGENTS_FILE: _agents_md(root, config)}
    if "claude" in config.agents.tools:
        files[CLAUDE_FILE] = _claude_md(_read(root, CLAUDE_FILE))
    if "gemini" in config.agents.tools:
        files[GEMINI_SETTINGS] = _gemini_settings(_read(root, GEMINI_SETTINGS))
    skills = _skills(root, config)
    skill_dirs = [f"{t}/{n}" for t in config.agents.skills_targets for n in skills]
    rendered: dict[str, bytes] = {}
    for target in config.agents.skills_targets:
        for name, skill in skills.items():
            for rel, content in skill.files.items():
                rendered[f"{target}/{name}/{rel}"] = content
    previous = _manifest(root)
    for path, content in rendered.items():
        on_disk = _read(root, path)
        if on_disk is not None and on_disk != content and path not in previous:
            raise SyncError(
                f"{path} exists but is not managed by devloop; move that skill to the "
                "project skills folder (skills.project) and delete the copy"
            )
    stale = {p for p in previous if p not in rendered}
    for folder in skill_dirs:
        base = root / folder
        if base.is_dir():
            managed = any(p.startswith(f"{folder}/") for p in previous)
            for found in base.rglob("*"):
                rel = found.relative_to(root).as_posix()
                if not found.is_file() or rel in rendered or "__pycache__" in rel:
                    continue
                if not managed:
                    raise SyncError(f"{rel} is in a skill folder devloop does not manage yet")
                stale.add(rel)
    files.update(rendered)
    manifest = {"files": sorted(rendered)}
    files[MANIFEST] = (json.dumps(manifest, indent=2) + "\n").encode()
    return files, {p for p in stale if (root / p).is_file()}


def plan(root: Path, config: Config) -> list[Change]:
    """Return what sync would change, without writing anything."""
    files, delete = desired(root, config)
    changes: list[Change] = []
    for path, content in sorted(files.items()):
        current = _read(root, path)
        if current is None:
            changes.append(Change(path, "create"))
        elif current != content:
            changes.append(Change(path, "update"))
    changes.extend(Change(p, "delete") for p in sorted(delete))
    return changes


def apply(root: Path, config: Config) -> list[Change]:
    """Bring the project to the rendered state and return what changed."""
    files, _ = desired(root, config)
    changes = plan(root, config)
    for change in changes:
        path = root / change.path
        if change.action == "delete":
            path.unlink()
            _prune(path.parent, root)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(files[change.path])
    return changes


def _prune(folder: Path, root: Path) -> None:
    """Remove ``folder`` and its empty parents, stopping at ``root``."""
    while folder != root and folder.is_dir() and not any(folder.iterdir()):
        folder.rmdir()
        folder = folder.parent

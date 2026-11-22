"""``devloop size``: report a pull request's size and flag one over ``pr.max_lines``.

It counts added plus deleted lines on GitHub's merge ref (``HEAD^1..HEAD``), leaving
out the paths in ``pr.exclude``, the marked blocks in ``pr.exclude_blocks`` and the
skill copies ``devloop sync`` writes. Every excluded file is named. Over the limit it
fails unless the PR has the ``pr.large_label`` label. The check is advisory: every
doubt (another event, a head that is not a merge ref, a git error, unreadable labels)
passes with a note.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

from devloop.config import Config, ExcludeBlock
from devloop.git import file_at, git, is_merge_ref
from devloop.github import current_labels
from devloop.sync import MANIFEST

TOP_FILES = 10
SYNC_REASON = "written by devloop sync"
_HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_DIFF = ("--no-ext-diff", "--no-textconv", "--no-renames", "HEAD^1", "HEAD")


@dataclass(frozen=True)
class FileCount:
    """Lines one file adds and deletes, and why any of them are not counted."""

    path: str
    added: int
    deleted: int
    reason: str = ""

    @property
    def lines(self) -> int:
        """Added plus deleted lines."""
        return self.added + self.deleted


@dataclass(frozen=True)
class Size:
    """A pull request's counted and excluded line changes."""

    counted: tuple[FileCount, ...]
    excluded: tuple[FileCount, ...]

    @property
    def total(self) -> int:
        """Counted added plus deleted lines."""
        return sum(item.lines for item in self.counted)

    @property
    def excluded_total(self) -> int:
        """Added plus deleted lines left out of the count."""
        return sum(item.lines for item in self.excluded)


def _numstat(root: Path) -> list[FileCount]:
    """Return each file's added and deleted lines on the merge ref; binary files count 0."""
    counts = []
    for record in git(root, "diff", "--numstat", "-z", *_DIFF).split("\0"):
        if record:
            added, deleted, path = record.split("\t", 2)
            counts.append(
                FileCount(
                    path,
                    int(added) if added.isdigit() else 0,
                    int(deleted) if deleted.isdigit() else 0,
                )
            )
    return counts


def _synced(root: Path) -> set[str]:
    """Return the skill copies the manifest at HEAD lists, plus the manifest itself."""
    try:
        files = json.loads(file_at(root, "HEAD", MANIFEST) or '{"files": []}')["files"]
    except (ValueError, KeyError, TypeError):
        return set()
    return {MANIFEST, *(f for f in files if isinstance(f, str))}


def _block_lines(text: str, start: str, end: str) -> set[int]:
    """Return the 1-based line numbers from each ``start`` line to its ``end`` line."""
    found: set[int] = set()
    opened: int | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        if opened is None and line.strip() == start:
            opened = number
        elif opened is not None and line.strip() == end:
            found.update(range(opened, number + 1))
            opened = None
    return found


def _changed_lines(diff: str) -> tuple[list[int], list[int]]:
    """Return (old line numbers deleted, new line numbers added) from a ``-U0`` diff."""
    deleted: list[int] = []
    added: list[int] = []
    old = new = 0
    in_hunk = False
    for line in diff.splitlines():
        header = _HUNK.match(line)
        if header:
            old, new, in_hunk = int(header.group(1)), int(header.group(2)), True
        elif not in_hunk:
            continue
        elif line.startswith("-"):
            deleted.append(old)
            old += 1
        elif line.startswith("+"):
            added.append(new)
            new += 1
    return deleted, added


def _split_block(root: Path, item: FileCount, block: ExcludeBlock) -> list[FileCount]:
    """Split ``item`` into the lines outside its marked block and those inside."""
    deleted, added = _changed_lines(git(root, "diff", "-U0", *_DIFF, "--", item.path))
    old = _block_lines(file_at(root, "HEAD^1", item.path), block.start, block.end)
    new = _block_lines(file_at(root, "HEAD", item.path), block.start, block.end)
    in_deleted = sum(1 for n in deleted if n in old)
    in_added = sum(1 for n in added if n in new)
    parts = [
        FileCount(item.path, item.added - in_added, item.deleted - in_deleted),
        FileCount(item.path, in_added, in_deleted, "the marked block, in pr.exclude_blocks"),
    ]
    return [part for part in parts if part.lines or not part.reason]


def _reason(path: str, patterns: Sequence[str], synced: set[str]) -> str:
    """Return why ``path`` is not counted, or ``""`` when it is."""
    if path in synced:
        return SYNC_REASON
    for pattern in patterns:
        if fnmatchcase(path, pattern):
            return f"matches pr.exclude {pattern!r}"
    return ""


def measure(root: Path, config: Config) -> Size | None:
    """Sort the PR's changes into counted and excluded; None when HEAD is not a merge ref."""
    if not is_merge_ref(root):
        return None
    synced = _synced(root)
    blocks = {b.path: b for b in config.pr.exclude_blocks}
    counted: list[FileCount] = []
    excluded: list[FileCount] = []
    for item in _numstat(root):
        reason = _reason(item.path, config.pr.exclude, synced)
        if reason:
            excluded.append(FileCount(item.path, item.added, item.deleted, reason))
        elif item.path in blocks and item.lines:
            for part in _split_block(root, item, blocks[item.path]):
                (excluded if part.reason else counted).append(part)
        else:
            counted.append(item)
    return Size(tuple(counted), tuple(excluded))


def report(size: Size) -> list[str]:
    """Return the counted total, the largest files and every excluded file."""
    lines = [f"Counted: {size.total} changed lines (added + deleted) in {len(size.counted)} files."]
    largest = sorted(size.counted, key=lambda i: (-i.lines, i.path))[:TOP_FILES]
    if largest:
        lines.append(f"Largest {len(largest)} files:")
        lines += [f"  {i.lines:6d}  (+{i.added} -{i.deleted})  {i.path}" for i in largest]
    lines.append(f"Excluded: {size.excluded_total} lines in {len(size.excluded)} files.")
    lines += [
        f"  {i.lines:6d}  {i.path}: {i.reason}"
        for i in sorted(size.excluded, key=lambda i: (-i.lines, i.path))
    ]
    return lines


def verdict(size: Size, labels: set[str] | None, config: Config) -> tuple[int, str]:
    """Return (exit code, closing message) for ``size`` and the PR's labels."""
    limit, label = config.pr.max_lines, config.pr.large_label
    if size.total <= limit:
        return 0, f"Within the limit: {size.total} of {limit} lines."
    over = f"This pull request changes {size.total} lines, over the {limit}-line limit."
    if labels is None:
        return 0, f"{over} The labels could not be read, so the check passes (advisory)."
    if label in labels:
        return 0, f"{over} It has the `{label}` label, so the check passes."
    return 1, (
        f"{over} Split it into PRs of one concern each, about {limit} lines or less, "
        f"stacked on each other's branches. If it cannot be split, add the `{label}` "
        "label and say why in the description."
    )


def run(
    event: str, event_path: str | None, root: Path, config: Config, env: Mapping[str, str]
) -> tuple[int, list[str]]:
    """Return (exit code, output lines) for ``event``; every doubt exits 0.

    Labels are read only for a PR over the limit, the one case where they matter.
    """
    if event != "pull_request":
        return 0, [f"devloop size: not a pull request ({event or 'no event'}); nothing to do."]
    try:
        size = measure(root, config)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        return 0, [f"devloop size: cannot read the change ({error}); passing (advisory)."]
    if size is None:
        return 0, ["devloop size: HEAD is not a merge ref; the size is unknown, passing."]
    if size.total <= config.pr.max_lines:
        return 0, [*report(size), verdict(size, set(), config)[1]]
    labels, source = current_labels(event_path, env)
    code, message = verdict(size, labels, config)
    return code, [*report(size), source, message]

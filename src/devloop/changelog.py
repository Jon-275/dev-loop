"""``devloop changelog``: check changelog fragments and build them into the changelog.

Each change adds one file, ``<changelog.dir>/<slug>.<section>.md``, instead of editing
the changelog, so open pull requests never edit the same lines. ``changelog.sections``
maps ``<section>`` to its ``###`` heading under ``## [Unreleased]``.

``check`` validates every fragment's name and section and that it is not empty.
``build`` inserts each fragment at the top of its section, in file name order, and
deletes it; an entry already there is not inserted again, so a second run changes
nothing.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

from devloop.config import Changelog
from devloop.git import git, is_merge_ref

NOT_FRAGMENTS = frozenset({"README.md"})
NAME = re.compile(r"^(?P<slug>[a-z0-9][a-z0-9-]*)\.(?P<section>[a-z]+)\.md$")
UNRELEASED = "## [Unreleased]"


@dataclass(frozen=True)
class Fragment:
    """One changelog fragment: its file and the entry text it holds."""

    path: Path
    section: str
    text: str

    @property
    def entry(self) -> list[str]:
        """Return the entry as list-item lines, adding ``- `` when the file omits it."""
        lines = self.text.strip("\n").splitlines()
        if lines[0].startswith("- "):
            return [line.rstrip() for line in lines]
        return ["- " + lines[0].rstrip(), *[f"  {line}".rstrip() for line in lines[1:]]]


def fragment_files(root: Path, log: Changelog) -> list[Path]:
    """Return the files in the fragment folder other than its README, sorted by name."""
    folder = root / log.dir
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_file() and p.name not in NOT_FRAGMENTS)


def check(root: Path, log: Changelog) -> list[str]:
    """Return one error per bad fragment name, unknown section or empty fragment."""
    errors: list[str] = []
    for path in fragment_files(root, log):
        match = NAME.match(path.name)
        if path.is_symlink():  # a symlink could pull any file's text into the changelog
            errors.append(f"{path.name}: is a symlink; a fragment must be a file")
        elif match is None:
            errors.append(f"{path.name}: name must be <slug>.<section>.md (lower-case slug)")
        elif match["section"] not in log.sections:
            errors.append(
                f"{path.name}: unknown section {match['section']!r}; "
                f"use one of {', '.join(log.sections)}"
            )
        elif not path.read_text(encoding="utf-8").strip():
            errors.append(f"{path.name}: empty fragment")
    return errors


def load(root: Path, log: Changelog) -> list[Fragment]:
    """Return every fragment in file name order; raise ``ValueError`` if any is invalid."""
    errors = check(root, log)
    if errors:
        raise ValueError("; ".join(errors))
    fragments = []
    for path in fragment_files(root, log):
        section = path.name.split(".")[-2]
        fragments.append(Fragment(path, section, path.read_text(encoding="utf-8")))
    return fragments


def entries_in(lines: Sequence[str]) -> set[tuple[str, ...]]:
    """Return the whole list items in ``lines``: a ``- `` line and its indented lines.

    Blank lines inside an item belong to it; it ends at the next top-level ``- `` line
    or an unindented line such as a heading.
    """
    found: set[tuple[str, ...]] = set()
    current: list[str] | None = None

    def close() -> None:
        """Record the item being read, without its trailing blank lines."""
        if current is not None:
            while current and current[-1] == "":
                current.pop()
            found.add(tuple(current))

    for line in lines:
        if line.startswith("- "):
            close()
            current = [line.rstrip()]
        elif current is not None and (line.startswith(" ") or not line.strip()):
            current.append(line.rstrip())
        elif current is not None:
            close()
            current = None
    close()
    return found


def _unreleased(lines: Sequence[str]) -> tuple[int, int]:
    """Return the Unreleased block's (heading index, end index) in ``lines``."""
    if UNRELEASED not in lines:
        raise ValueError(f"the changelog has no {UNRELEASED!r} heading")
    start = list(lines).index(UNRELEASED)
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return start, end


def _section(lines: Sequence[str], heading: str) -> tuple[int, int] | None:
    """Return the (first body line, end) of ``heading`` in Unreleased, or None."""
    start, end = _unreleased(lines)
    try:
        at = list(lines).index(heading, start, end) + 1
    except ValueError:
        return None
    return at, next((i for i in range(at, end) if lines[i].startswith("### ")), end)


def present(text: str, fragment: Fragment, log: Changelog) -> bool:
    """Return whether ``fragment``'s whole entry is in its Unreleased section."""
    lines = text.splitlines()
    span = _section(lines, f"### {log.sections[fragment.section]}")
    return span is not None and tuple(fragment.entry) in entries_in(lines[span[0] : span[1]])


def insert(text: str, fragments: Sequence[Fragment], log: Changelog) -> str:
    """Return ``text`` with each fragment at the top of its Unreleased section."""
    lines = text.splitlines()
    _unreleased(lines)
    for section, title in log.sections.items():
        entries = [f.entry for f in fragments if f.section == section]
        heading = f"### {title}"
        span = _section(lines, heading)
        if span is None:
            if entries:
                raise ValueError(f"the Unreleased block has no {heading!r} heading")
            continue
        at, stop = span
        # Whole entries only: "- Fix parsing" is new beside "- Fix parsing for JSON",
        # and two identical fragments in one build insert one entry.
        known = entries_in(lines[at:stop])
        new = []
        for entry in entries:
            if tuple(entry) not in known:
                known.add(tuple(entry))
                new.append(entry)
        if not new:
            continue
        added = [line for entry in new for line in entry]
        first = next((i for i in range(at, stop) if lines[i].startswith("- ")), None)
        if first is None:  # an empty section: after any intro, before the blank lines
            first = stop
            while first > at and lines[first - 1] == "":
                first -= 1
            added = ["", *added, ""] if first == stop < len(lines) else ["", *added]
        lines[first:first] = added
    return "\n".join(lines) + "\n"


def build(root: Path, log: Changelog, dry_run: bool = False) -> str:
    """Insert the fragments into the changelog and delete them; return the result.

    With ``dry_run`` nothing is written or deleted.
    """
    target = root / log.file
    current = target.read_text(encoding="utf-8")
    fragments = load(root, log)
    if not fragments:
        return current
    result = insert(current, fragments, log)
    if not dry_run:
        if result != current:
            target.write_text(result, encoding="utf-8")
        for fragment in fragments:  # only a fragment whose entry landed is deleted
            if present(result, fragment, log):
                fragment.path.unlink()
    return result


def is_fragment(path: str, log: Changelog) -> bool:
    """Return whether ``path`` is a validly named fragment directly in the fragment folder."""
    folder, _, name = path.rpartition("/")
    match = NAME.match(name)
    return folder == log.dir and match is not None and match["section"] in log.sections


def needs_entry(changed: Sequence[tuple[str, str]], log: Changelog) -> bool:
    """Return whether ``changed`` touches code but adds no fragment or changelog edit.

    ``changed`` holds (git status letter, path) pairs. Only an added or modified valid
    fragment, or an edit to the changelog, counts as an entry.
    """
    if any(
        status in "ACMR" and (path == log.file or is_fragment(path, log))
        for status, path in changed
    ):
        return False
    return any(fnmatchcase(path, pattern) for _, path in changed for pattern in log.code)


def changed_files(root: Path) -> list[tuple[str, str]] | None:
    """Return the PR's (status, path) pairs, or None when HEAD is not a merge ref."""
    if not is_merge_ref(root):
        return None
    out = git(root, "diff", "--name-status", "--diff-filter=ACMRD", "HEAD^1", "HEAD")
    return [(f[0][0], f[-1]) for f in (line.split("\t") for line in out.splitlines())]

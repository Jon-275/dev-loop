"""Read a pull request through Git only: diffs, file lists and file contents.

The working directory is the pull request's checkout, which is untrusted data:

- every ``git diff`` runs with ``--no-ext-diff --no-textconv``, so the pull request's
  ``.gitattributes`` cannot name a program to run;
- file contents are read from Git objects at the head commit, never the working tree,
  and symlinks are skipped, so a link to ``/proc/self/environ`` cannot send local files
  or the environment to a model;
- no inherited ``GIT_*`` variable reaches git, so none can point it elsewhere.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path

from devloop.review.model import DiffLines

SYMLINK_MODE = "120000"


def git_environment() -> dict[str, str]:
    """Return this process's environment without any ``GIT_*`` variable.

    Git then finds the repository from the working directory alone: an inherited
    ``GIT_DIR``, ``GIT_WORK_TREE``, ``GIT_INDEX_FILE`` or ``GIT_CONFIG_*`` cannot
    point it at another repository or add configuration.
    """
    return {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}


def run_git(args: list[str], cwd: str | Path | None = None) -> str:
    """Run git with ``args`` in ``cwd`` (default: the working directory) and return stdout.

    It only reads (``diff``, ``ls-tree``, ``cat-file``); it never writes config.
    """
    return subprocess.check_output(  # nosec B603 - fixed git arguments
        ["git", *args], text=True, cwd=Path.cwd() if cwd is None else cwd, env=git_environment()
    )


#: The working directory is the pull request's checkout, so its ``.gitattributes``
#: is untrusted: no external diff program or textconv filter is ever run.
SAFE_DIFF = ("diff", "--no-ext-diff", "--no-textconv")


def run_git_diff(args: list[str]) -> str:
    """Run ``git diff`` with ``args``, never through a diff driver the repository names."""
    return run_git([*SAFE_DIFF, *args])


def changed_files(base_sha: str, head_sha: str, files: Sequence[str]) -> list[str]:
    """Return every path matching ``files`` that the pull request changes, deletions included."""
    out = run_git_diff(["--name-only", "-z", f"{base_sha}...{head_sha}", "--", *files])
    return [path for path in out.split("\0") if path]


def unshown_files(base_sha: str, head_sha: str, shown: Sequence[str]) -> list[str]:
    """Return the paths the pull request changes that the reviewed diff leaves out.

    These are every changed path outside the reviewed file types (for example
    ``uv.lock`` or docs); only their names reach the prompt, never their contents.
    """
    out = run_git_diff(["--name-only", "-z", f"{base_sha}...{head_sha}"])
    reviewed = set(shown)
    return [path for path in out.split("\0") if path and path not in reviewed]


def changed_between(start: str, head: str, files: Sequence[str]) -> list[str]:
    """Return the reviewed paths that differ between ``start`` and ``head``, both sides of a rename.

    Without rename detection a deletion is never hidden behind a similar file.
    """
    refs = [ref for ref in (start, head) if ref]
    out = run_git_diff(["--no-renames", "--name-only", "-z", *refs, "--", *files])
    return [path for path in out.split("\0") if path]


def reviewable_blobs(head_sha: str, paths: list[str]) -> list[str]:
    """Return the paths that are regular files at ``head_sha``.

    Deleted paths are absent from the tree and symlinks have mode 120000;
    both are left out, so only real file contents are ever read.
    """
    if not paths:
        return []
    out = run_git(["--literal-pathspecs", "ls-tree", "-z", head_sha, "--", *paths])
    regular: set[str] = set()
    for entry in out.split("\0"):
        if not entry:
            continue
        meta, path = entry.split("\t", 1)
        mode, kind, _ = meta.split(" ", 2)
        if kind == "blob" and mode != SYMLINK_MODE:
            regular.add(path)
    return [path for path in paths if path in regular]


def file_contents(head_sha: str, path: str) -> str:
    """Return the contents of ``path`` at ``head_sha`` from the Git object store."""
    return run_git(["cat-file", "blob", f"{head_sha}:{path}"])


_GIT_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}


def unquote_git_path(text: str) -> str:
    r"""Return a path from a diff header, with Git's C-style quoting undone.

    Git quotes a path holding non-ASCII bytes, quotes, backslashes or control
    characters (``core.quotePath``), for example ``"b/caf\303\251.py"``.
    """
    if len(text) < 2 or not (text.startswith('"') and text.endswith('"')):
        return text
    out = bytearray()
    body = text[1:-1]
    index = 0
    while index < len(body):
        char = body[index]
        if char != "\\":
            out += char.encode()
            index += 1
            continue
        escape = body[index + 1 : index + 2]
        if escape in _GIT_ESCAPES:
            out.append(_GIT_ESCAPES[escape])
            index += 2
        elif re.fullmatch(r"[0-7]{3}", body[index + 1 : index + 4]):
            out.append(int(body[index + 1 : index + 4], 8))
            index += 4
        else:  # not an escape Git writes; keep it as is
            out += char.encode()
            index += 1
    return out.decode("utf-8", errors="replace")


def header_path(text: str) -> str:
    r"""Return the repository path in a ``--- a/...`` or ``+++ b/...`` header.

    Git ends the header with a tab when an unquoted path holds a space; a
    quoted path never holds a raw tab (Git writes it as ``\t``), so
    everything from the first tab on is the separator, not the path.
    """
    path = unquote_git_path(text.split("\t", 1)[0])
    return path[2:] if path[:2] in ("a/", "b/") else path


_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def diff_lines(diff_text: str) -> dict[str, DiffLines]:
    """Map each file in a unified diff to the lines GitHub accepts comments on.

    Hunk bodies are read by their line counts, so a changed line whose text
    starts with ``--`` or ``++`` is never mistaken for a file header.
    """
    files: dict[str, DiffLines] = {}
    current: DiffLines | None = None
    old = new = old_left = new_left = 0
    for line in diff_text.splitlines():
        if current is not None and (old_left > 0 or new_left > 0):
            if line.startswith("+"):
                current.right.add(new)
                new, new_left = new + 1, new_left - 1
            elif line.startswith("-"):
                current.left.add(old)
                old, old_left = old + 1, old_left - 1
            elif line.startswith(" ") or line == "":
                current.right.add(new)
                old, new, old_left, new_left = old + 1, new + 1, old_left - 1, new_left - 1
            continue  # a "No newline at end of file" marker
        if line.startswith("diff --git "):
            current = None
        elif line.startswith("--- ") and line[4:] != "/dev/null":
            # Keyed by the old path until "+++" names the new one; a deleted
            # file ("+++ /dev/null") keeps its old path.
            current = files.setdefault(header_path(line[4:]), DiffLines())
        elif line.startswith("+++ ") and line[4:] != "/dev/null":
            current = files.setdefault(header_path(line[4:]), DiffLines())
        elif (match := _HUNK.match(line)) is not None and current is not None:
            old, new = int(match.group(1)), int(match.group(3))
            old_left = int(match.group(2)) if match.group(2) is not None else 1
            new_left = int(match.group(4)) if match.group(4) is not None else 1
    return {path: lines for path, lines in files.items() if lines.right or lines.left}


def split_diff(diff_text: str) -> dict[str, str]:
    """Each file's section of a ``git diff``, keyed by its path, in diff order.

    The path is the one :func:`diff_lines` uses (the new path, or the old one for
    a deletion). A section with no ``---``/``+++`` header (a binary or mode-only
    change) is keyed by the ``b/`` path of its ``diff --git`` line.
    """
    sections: list[list[str]] = []
    for line in diff_text.splitlines(keepends=True):
        if line.startswith("diff --git ") or not sections:
            sections.append([])
        sections[-1].append(line)
    parts: dict[str, str] = {}
    for section in sections:
        text = "".join(section)
        old = new = ""
        for line in section:
            if line.startswith("--- ") and line[4:].rstrip("\n") != "/dev/null":
                old = header_path(line[4:].rstrip("\n"))
            elif line.startswith("+++ ") and line[4:].rstrip("\n") != "/dev/null":
                new = header_path(line[4:].rstrip("\n"))
            elif line.startswith("@@"):
                break
        path = new or old
        if not path and section[0].startswith("diff --git "):
            path = _diff_git_new_path(section[0])
        parts[path] = parts.get(path, "") + text
    return parts


def _diff_git_new_path(line: str) -> str:
    r"""Return the ``b/`` path of a ``diff --git a/... b/...`` line, unquoted.

    Git quotes a path holding non-ASCII bytes or control characters
    (``diff --git "a/caf\303\251.py" "b/caf\303\251.py"``), so a quoted ``b``
    path is read from its opening quote and unquoted (:func:`unquote_git_path`).
    """
    rest = line.rstrip("\n").removeprefix("diff --git ")
    start = rest.rfind(' "b/')
    if rest.endswith('"') and start != -1:
        return unquote_git_path(rest[start + 1 :])[2:]
    return rest.rpartition(" b/")[2]


def renames(diff_text: str) -> dict[str, str]:
    """Each renamed file's old path mapped to its new one, from a ``git diff``."""
    found: dict[str, str] = {}
    old = ""
    for line in diff_text.splitlines():
        if line.startswith("rename from "):
            old = header_path("a/" + line.removeprefix("rename from "))
        elif line.startswith("rename to ") and old:
            found[old] = header_path("b/" + line.removeprefix("rename to "))
    return found


def hunk_count(diff_text: str) -> int:
    """Return the number of hunks in a ``git diff``."""
    return sum(1 for line in diff_text.splitlines() if line.startswith("@@ "))

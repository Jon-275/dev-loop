"""``devloop changes``: tell CI whether a pull request changes only documentation.

It writes two outputs for GitHub Actions:

- ``code``: ``false`` only when the event is a pull request and every file it changes
  matches ``docs.files``. Any doubt (another event, a head that is not a merge ref, a
  git error, an empty diff) gives ``true``, so every check runs.
- ``doc_tests``: the test files matching ``docs.tests`` that mention a ``.md`` file. A
  docs-only run still runs them, because they read documents.

Nothing under ``.github/`` or a skills folder is documentation, so a workflow or skill
change always runs every check.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable, Sequence
from fnmatch import fnmatchcase
from pathlib import Path

from devloop.config import Docs
from devloop.git import git, is_merge_ref


def is_document(path: str, docs: Docs, code_dirs: Sequence[str] = ()) -> bool:
    """Return whether ``path`` is documentation (case-insensitive match on ``docs.files``).

    Paths under ``.github/`` or ``code_dirs`` (the skills folders) are never documentation.
    """
    if any(path.startswith(f"{folder}/") for folder in (".github", *code_dirs)):
        return False
    return any(fnmatchcase(path.lower(), pattern.lower()) for pattern in docs.files)


def docs_only(paths: Iterable[str], docs: Docs, code_dirs: Sequence[str] = ()) -> bool:
    """Return whether ``paths`` is non-empty and every path in it is documentation."""
    changed = list(paths)
    return bool(changed) and all(is_document(path, docs, code_dirs) for path in changed)


def doc_tests(root: Path, docs: Docs) -> list[str]:
    """Return, sorted, every tracked test file matching ``docs.tests`` that mentions ``.md``."""
    found = []
    for rel in sorted(git(root, "ls-files", "-z").split("\0")):
        if rel and any(fnmatchcase(rel, pattern) for pattern in docs.tests):
            if ".md" in (root / rel).read_text(encoding="utf-8", errors="replace"):
                found.append(rel)
    return found


def decide(
    event: str, root: Path, docs: Docs, code_dirs: Sequence[str] = ()
) -> tuple[bool, list[str]]:
    """Return (code changed, changed files) for ``event``; every doubt is "code changed"."""
    if event != "pull_request":
        return True, []
    try:
        if not is_merge_ref(root):
            return True, []
        names = git(root, "diff", "--name-only", "-z", "--no-renames", "HEAD^1", "HEAD")
    except (OSError, subprocess.CalledProcessError):
        return True, []
    files = [name for name in names.split("\0") if name]
    return not docs_only(files, docs, code_dirs), files

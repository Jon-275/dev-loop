"""Incremental reviews: after a completed review, review only the pull request's own changes.

The last head a reviewer completed (:func:`devloop.review.github.previous_review`) is merged
with the base branch as far as the new head has merged it; the diff from that tree holds
the pull request's own changes since then and leaves out what came from the base. Every
merge runs with no attributes from the pull request, so it cannot pick a merge driver.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass

from devloop.review.gitread import (
    EMPTY_TREE,
    SAFE_DIFF,
    changed_between,
    git_environment,
    hunk_count,
    run_git,
    run_git_diff,
    split_diff,
)
from devloop.review.model import Baseline, Increment, ReviewError
from devloop.review.prompt import prompt_path

#: The empty tree. Merges read their attributes from it, so the pull request's
#: ``.gitattributes`` cannot pick a merge driver (``merge=union`` would make a
#: conflicted merge look clean, and so look like a plain merge of the base).
#: ``git merge-tree`` as the reviewers run it: no attributes from the pull request,
#: and it writes only objects, never a ref, the index, the working tree or config.
SAFE_MERGE_TREE = (
    f"--attr-source={EMPTY_TREE}",
    "merge-tree",
    "--write-tree",
    "--no-messages",
    "--name-only",
    "-z",
)
#: The most first-parent commits a push may add and still be called merge-only.
MAX_MERGE_WALK = 20
_SHA = re.compile(r"[0-9a-f]{40}")
#: The most carried-over file names the prompt lists; the rest are counted.
MAX_OTHER_PART_FILES = 50


def _git_code(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a read-only git command whose exit code is the answer; never raise on it."""
    return subprocess.run(  # nosec B603 - fixed git arguments
        ["git", *args], text=True, capture_output=True, env=git_environment(), check=False
    )


def is_ancestor(older: str, newer: str) -> bool:
    """Return whether commit ``older`` is ``newer`` or one of its ancestors.

    A commit missing from the checkout (a force push dropped it) is not an ancestor.

    Raises:
        ReviewError: git failed for another reason.

    """
    if _git_code(["cat-file", "-e", f"{older}^{{commit}}"]).returncode != 0:
        return False
    result = _git_code(["merge-base", "--is-ancestor", older, newer])
    if result.returncode not in (0, 1):
        raise ReviewError(f"git merge-base failed ({result.returncode}): {result.stderr[:300]}")
    return result.returncode == 0


def merge_tree(ours: str, theirs: str) -> tuple[str, tuple[str, ...] | None]:
    """Return the tree of merging ``theirs`` into ``ours``, and its conflicted paths.

    The paths are ``()`` for a clean merge and ``None`` for a conflict git
    names no path for; a conflicted file holds conflict markers in the tree.

    Raises:
        ReviewError: git could not merge at all (a missing object, an old git).

    """
    result = _git_code([*SAFE_MERGE_TREE, ours, theirs])
    tree, *names = result.stdout.split("\0")
    if result.returncode not in (0, 1) or not _SHA.fullmatch(tree):
        raise ReviewError(f"git merge-tree failed ({result.returncode}): {result.stderr[:300]}")
    conflicts = tuple(name for name in names if name)
    return tree, (None if result.returncode == 1 and not conflicts else conflicts)


def merge_only(since: str, head: str, base: str) -> bool:
    """Return whether every commit ``head`` adds after ``since`` is a clean merge of the base.

    Each commit on the first-parent chain from ``head`` back to ``since`` must
    be a two-parent merge whose second parent is on the base branch (an
    ancestor of ``base``) and whose tree is exactly the clean merge of its
    parents: a resolved conflict or any extra change is not merge-only.
    """
    commit = head
    for _ in range(MAX_MERGE_WALK):
        if commit == since:
            return commit != head
        parents = run_git(["rev-list", "--parents", "-n", "1", commit]).split()[1:]
        if len(parents) != 2 or not is_ancestor(since, parents[0]):
            return False
        ours, theirs = parents
        tree = run_git(["rev-parse", f"{commit}^{{tree}}"]).strip()
        if not is_ancestor(theirs, base) or merge_tree(ours, theirs) != (tree, ()):
            return False
        commit = ours
    return False


def increment_start(
    previous: Baseline | None, base: str, head: str, files: Sequence[str]
) -> str | None:
    """Return the tree to diff ``head`` against for an incremental review (``None``: full).

    That tree is the reviewed head merged with the base branch as far as
    ``head`` has merged it, so the diff from it holds the pull request's own
    changes since then and leaves out what came from the base. A full review
    is due when there is no earlier review, ``head`` is that head (a re-run),
    it is not an ancestor (a force push), the base did not only move forward
    (a retarget), or the merge conflicts in a reviewed file (a resolution to
    review). A conflict only in files no reviewer reads (``uv.lock``, docs)
    does not count: the diff never shows those files.
    """
    if previous is None or previous.head == head:
        return None
    if not (is_ancestor(previous.head, head) and is_ancestor(previous.base, base)):
        return None
    fork = run_git(["merge-base", base, head]).strip()
    tree, conflicts = merge_tree(previous.head, fork)
    if conflicts is None:
        return None
    if conflicts:  # git's own pathspecs decide which conflicted files are reviewed
        reviewed = {
            p
            for ref in (tree, previous.head, fork)
            for p in changed_between(EMPTY_TREE, ref, files)
        }
        if reviewed.intersection(conflicts):
            return None
    return tree


def increment_note(increment: Increment) -> str:
    """Return the prompt's note that the diff is incremental, and the files it leaves out."""
    names = [prompt_path(path) for path, _ in increment.carried[:MAX_OTHER_PART_FILES]]
    more = len(increment.carried) - len(names)
    note = (
        "\nThis is an incremental review: the diff holds only the pull request's own changes "
        f"since commit {increment.since[:12]}, which was reviewed earlier; changes merged in "
        "from the base branch are left out. Do not report code outside this diff as missing."
    )
    if names:
        note += f" Changed files reviewed then and unchanged since: {', '.join(names)}"
        note += f" (and {more} more)" if more > 0 else ""
    return note + "\n"


@dataclass(frozen=True)
class Plan:
    """What a review reads: the diff, its paths, and the increment (``None`` for a full one).

    ``nothing_new`` is set, with the status to report, when nothing needs a review.
    """

    diff: str
    paths: list[str]
    increment: Increment | None = None
    nothing_new: str = ""


def plan(
    baseline: Baseline | None,
    base: str,
    head: str,
    files: Sequence[str],
    full_diff: str,
    changed: list[str],
) -> Plan:
    """Return what to review: the whole diff, only what changed since ``baseline``, or nothing.

    Nothing is reviewed when every commit since then only merges the base branch, or the
    reviewed files equal that head merged with the base. File names from the pull request
    are literal pathspecs, and renames are read as a deletion and an addition, so neither
    side of a rename is hidden.
    """
    start = increment_start(baseline, base, head, files)
    if baseline is None or start is None:
        return Plan(full_diff, changed)
    if merge_only(baseline.head, head, base):
        return Plan(
            "",
            [],
            nothing_new=(
                f"No new changes since {baseline.head[:12]}; merge of the base branch only"
            ),
        )
    since = changed_between(start, head, files)
    every = changed_between(f"{base}...{head}", "", files)
    paths = [path for path in since if path in every]
    if not paths:
        return Plan(
            "",
            [],
            nothing_new=(f"No reviewed changes since {baseline.head[:12]}; nothing new to review"),
        )
    interdiff = run_git(
        ["--literal-pathspecs", *SAFE_DIFF, "--no-renames", start, head, "--", *paths]
    )
    whole = run_git_diff(["--no-renames", f"{base}...{head}", "--", *files])
    parts = split_diff(whole)
    increment = Increment(
        since=baseline.head,
        carried=tuple((p, hunk_count(parts.get(p, ""))) for p in every if p not in paths),
        restored=tuple(path for path in since if path not in every),
        hunks=hunk_count(interdiff),
        total_hunks=hunk_count(whole),
        files=len(paths),
        total_files=len(every),
    )
    return Plan("".join(split_diff(interdiff).values()), paths, increment)

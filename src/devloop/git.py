"""Small git helpers shared by the PR checks."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def git(root: Path, *args: str) -> str:
    """Run git in ``root`` with no inherited ``GIT_*`` variables and return its output."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    done = subprocess.run(
        ["git", "-C", str(root), *args], env=env, check=True, capture_output=True, text=True
    )
    return done.stdout


def is_merge_ref(root: Path) -> bool:
    """Return whether HEAD has two parents, as GitHub's pull request merge ref does.

    Its first parent is the base branch, so ``HEAD^1..HEAD`` is exactly the PR's change.
    """
    return len(git(root, "rev-list", "--parents", "-n", "1", "HEAD").split()) == 3


def file_at(root: Path, revision: str, path: str) -> str:
    """Return ``path``'s text at ``revision``, or ``""`` when it does not exist there."""
    try:
        return git(root, "show", f"{revision}:{path}")
    except subprocess.CalledProcessError:
        return ""

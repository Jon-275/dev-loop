"""Shared helpers for the review tests: a test provider, git and diff sections."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from devloop.review.model import Provider

PROVIDER = Provider(
    name="Testbot",
    status_context="Testbot review",
    header="### Testbot review",
    marker_prefix="testbot-finding",
    summary_marker="testbot-review",
    key_name="TESTBOT_API_KEY",
    effort_label="thinking",
    is_transient=lambda error: isinstance(error, TimeoutError),
    describe_error=lambda error: type(error).__name__,
    lens="You are a test reviewer.\n",
)


def run(repo: Path, *args: str) -> str:
    """Run git in ``repo`` with a fixed identity and no inherited ``GIT_*``; return stdout."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")} | {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    return subprocess.check_output(["git", *args], cwd=repo, text=True, env=env).strip()


def commit(repo: Path, message: str) -> str:
    """Commit everything in ``repo`` and return the new SHA."""
    run(repo, "add", "-A")
    run(repo, "commit", "-q", "-m", message)
    return run(repo, "rev-parse", "HEAD")


def section(path: str, line: int = 2) -> str:
    """Return one file's section of a unified diff, adding lines up to ``line``."""
    return (
        f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
        f"@@ -1,1 +1,{line} @@\n a = 1\n" + "".join(f"+b{n} = {n}\n" for n in range(2, line + 1))
    )

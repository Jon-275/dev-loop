"""Real pushes through the hook ``devloop pr-open --install`` writes, to a local bare repo.

A fake ``gh`` says every ``merged-*`` branch has a merged PR and nothing else has one; a
fake ssh sends the push to a bare repository on disk.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from devloop.pr_open import PRE_COMMIT_MARKER

FAKE_GH = """#!/bin/sh
# Fake gh: a merged PR #7 for merged-* branches, no PRs otherwise.
echo "$*" >> "$FAKE_GH_LOG"
for arg in "$@"; do
    case "$arg" in
        --head=merged-*) echo '[{"state":"MERGED","number":7,"url":"u"}]'; exit 0 ;;
    esac
done
echo '[]'
"""
FOREIGN = "#!/bin/sh\nexit 0\n"


@dataclass
class Clone:
    """A clone that pushes to a local bare repo, with fake gh and devloop on PATH."""

    work: Path
    bare: Path
    log: Path
    bin: Path
    env: dict[str, str]
    git: Callable[..., subprocess.CompletedProcess[str]]

    def install(self) -> subprocess.CompletedProcess[str]:
        """Run ``devloop pr-open --install`` in the clone."""
        return subprocess.run(
            [str(self.bin / "devloop"), "pr-open", "--install"],
            cwd=self.work,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def remote_branches(self) -> str:
        """Return the branches the bare remote has."""
        return self.git("--git-dir", str(self.bare), "branch", "--list").stdout


def _script(path: Path, text: str) -> None:
    """Write an executable script."""
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def clone(tmp_path: Path) -> Clone:
    """Build the clone, its bare remote, and branches ``ok-b2`` and ``merged-b2``."""
    bare, work, bin_dir = tmp_path / "remote.git", tmp_path / "work", tmp_path / "bin"
    bin_dir.mkdir()
    _script(bin_dir / "gh", FAKE_GH)
    _script(bin_dir / "fake-ssh", f'#!/bin/sh\nexec git-receive-pack "{bare}"\n')
    _script(bin_dir / "devloop", f'#!/bin/sh\nexec "{sys.executable}" -m devloop.cli "$@"\n')
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
        "PATH": os.pathsep.join([str(bin_dir), os.environ["PATH"]]),
        "GIT_SSH_COMMAND": str(bin_dir / "fake-ssh"),
        "FAKE_GH_LOG": str(tmp_path / "gh.log"),
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    env.pop("ALLOW_PUSH_TO_FINISHED_PR", None)

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        """Run git in the clone."""
        return subprocess.run(
            ["git", *args], cwd=work, env=env, capture_output=True, text=True, timeout=60
        )

    subprocess.run(["git", "init", "-q", "--bare", str(bare)], env=env, check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], env=env, check=True)
    (work / "a.txt").write_text("a\n", encoding="utf-8")
    for args in (
        ("add", "."),
        ("commit", "-q", "-m", "base"),
        ("remote", "add", "origin", "ssh://git@github.com/acme/app.git"),
        ("branch", "ok-b2"),
        ("branch", "merged-b2"),
    ):
        assert git(*args).returncode == 0
    return Clone(work, bare, tmp_path / "gh.log", bin_dir, env, git)


def test_one_finished_branch_refuses_the_whole_push(clone: Clone) -> None:
    assert clone.install().returncode == 0
    done = clone.git("push", "origin", "ok-b2", "merged-b2")
    assert done.returncode != 0
    assert "'merged-b2' (#7" in done.stderr
    assert clone.remote_branches() == ""
    log = clone.log.read_text(encoding="utf-8")
    assert "--head=ok-b2" in log and "--head=merged-b2" in log
    assert "--repo=github.com/acme/app" in log


def test_a_push_of_open_branches_goes_through(clone: Clone) -> None:
    assert clone.install().returncode == 0
    assert clone.git("branch", "ok-b3").returncode == 0
    done = clone.git("push", "origin", "ok-b2", "ok-b3")
    assert done.returncode == 0, done.stderr
    assert "ok-b3" in clone.remote_branches()


def test_an_amended_finished_branch_is_refused_even_with_force(clone: Clone) -> None:
    assert clone.install().returncode == 0
    assert clone.git("checkout", "-q", "merged-b2").returncode == 0
    assert clone.git("commit", "-q", "--amend", "-m", "review fix").returncode == 0
    done = clone.git("push", "--force", "origin", "merged-b2")
    assert done.returncode != 0
    assert "'merged-b2' (#7" in done.stderr


def test_core_hooks_path_is_where_the_guard_goes(clone: Clone) -> None:
    assert clone.git("config", "core.hooksPath", ".githooks").returncode == 0
    assert clone.install().returncode == 0
    assert (clone.work / ".githooks" / "pre-push").exists()
    assert not (clone.work / ".git" / "hooks" / "pre-push").exists()
    assert clone.git("push", "origin", "merged-b2").returncode != 0


@pytest.mark.parametrize("hooks_path", [None, ".githooks"])
def test_another_tools_hook_is_never_replaced(clone: Clone, hooks_path: str | None) -> None:
    hooks = clone.work / (hooks_path or ".git/hooks")
    hooks.mkdir(exist_ok=True)
    _script(hooks / "pre-push", FOREIGN)
    if hooks_path:
        assert clone.git("config", "core.hooksPath", hooks_path).returncode == 0
    done = clone.install()
    assert done.returncode == 2 and "another pre-push hook" in done.stderr
    assert (hooks / "pre-push").read_text(encoding="utf-8") == FOREIGN


def test_reinstalling_is_idempotent(clone: Clone) -> None:
    assert clone.install().returncode == 0
    assert clone.install().returncode == 0


def test_under_pre_commit_the_guard_becomes_the_legacy_hook(clone: Clone) -> None:
    hooks = clone.work / ".git" / "hooks"
    _script(hooks / "pre-push", f"#!/bin/sh\n{PRE_COMMIT_MARKER}\nexit 0\n")
    assert clone.install().returncode == 0
    assert "# devloop pre-push guard" in (hooks / "pre-push.legacy").read_text(encoding="utf-8")
    assert PRE_COMMIT_MARKER in (hooks / "pre-push").read_text(encoding="utf-8")


def test_a_hook_that_only_mentions_pre_commit_is_not_taken_for_it(clone: Clone) -> None:
    hooks = clone.work / ".git" / "hooks"
    _script(hooks / "pre-push", "#!/bin/sh\n# not using pre-commit here\nexit 0\n")
    assert clone.install().returncode == 2
    assert not (hooks / "pre-push.legacy").exists()


def test_a_broken_guard_refuses_the_push_unless_overridden(clone: Clone) -> None:
    assert clone.install().returncode == 0
    _script(clone.bin / "devloop", "#!/bin/sh\necho crash >&2\nexit 1\n")
    done = clone.git("push", "origin", "ok-b2")
    assert done.returncode != 0
    assert "ALLOW_PUSH_TO_FINISHED_PR" in done.stderr
    assert clone.remote_branches() == ""
    clone.env["ALLOW_PUSH_TO_FINISHED_PR"] = "1"
    assert clone.git("push", "origin", "ok-b2").returncode == 0


def test_without_devloop_the_push_is_not_checked_and_says_so(clone: Clone) -> None:
    assert clone.install().returncode == 0
    (clone.bin / "devloop").unlink()
    git_dir = os.path.dirname(shutil.which("git") or "/usr/bin/git")
    clone.env["PATH"] = os.pathsep.join([str(clone.bin), git_dir, "/usr/bin", "/bin"])
    done = clone.git("push", "origin", "merged-b2")
    assert done.returncode == 0
    assert "devloop is not installed; push not checked" in done.stderr


def test_install_fails_when_git_would_not_run_the_guard(clone: Clone) -> None:
    hooks = clone.work / ".git" / "hooks"
    (hooks / "pre-push").write_text(f"#!/bin/sh\n{PRE_COMMIT_MARKER}\n", encoding="utf-8")
    (hooks / "pre-push").chmod(0o644)  # pre-commit's hook, but git cannot run it
    done = clone.install()
    assert done.returncode == 2
    assert "git would not run it" in done.stderr

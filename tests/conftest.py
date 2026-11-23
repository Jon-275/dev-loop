"""Shared fixtures: real git repositories shaped like GitHub's pull request merge ref."""

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

MergeRepo = Callable[..., Path]


def git(cwd: Path, *args: str) -> str:
    """Run git in ``cwd`` with a fixed identity and no inherited ``GIT_*`` variables."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@example.invalid",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@example.invalid",
    )
    done = subprocess.run(
        ["git", "-C", str(cwd), *args], env=env, check=True, capture_output=True, text=True
    )
    return done.stdout


def lines(count: int, tag: str = "line") -> str:
    """Return ``count`` distinct lines of text."""
    return "".join(f"{tag} {n}\n" for n in range(count))


def _write(repo: Path, files: dict[str, str]) -> None:
    """Write ``files`` into ``repo``, deleting any whose text is empty."""
    for name, text in files.items():
        path = repo / name
        if text == "" and path.exists():
            path.unlink()
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


@pytest.fixture
def merge_repo(tmp_path: Path) -> MergeRepo:
    """Build a repo whose HEAD merges a PR into main, like GitHub's merge ref.

    ``changed`` is what the PR writes (an empty text deletes the file), ``base`` what
    exists before it branches. Main then adds 2,000 lines of its own, which must never
    count as part of the PR.
    """

    def build(changed: dict[str, str], base: dict[str, str] | None = None) -> Path:
        repo = tmp_path / "repo"
        repo.mkdir()
        git(repo, "init", "-q", "-b", "main")
        _write(repo, {"README.md": "start\n", **(base or {})})
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "base")
        git(repo, "checkout", "-q", "-b", "pr")
        _write(repo, changed)
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "pr")
        git(repo, "checkout", "-q", "main")
        _write(repo, {"main_only.py": lines(2000, "main")})
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "main moves")
        git(repo, "merge", "-q", "--no-ff", "-m", "merge", "pr")
        return repo

    return build


@pytest.fixture(autouse=True)
def _no_inherited_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every ``GIT_*`` variable before each test, as a git hook would leave them set."""
    for name in [n for n in os.environ if n.startswith("GIT_")]:
        monkeypatch.delenv(name)


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Return a repository whose base commit holds two Python files and a README (the cwd)."""
    from review_helpers import run

    run(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "keep.py").write_text("x = 1\n")
    (tmp_path / "gone.py").write_text("y = 2\n")
    (tmp_path / "README.md").write_text("hi\n")
    run(tmp_path, "add", ".")
    run(tmp_path, "commit", "-q", "-m", "base")
    monkeypatch.chdir(tmp_path)
    return tmp_path

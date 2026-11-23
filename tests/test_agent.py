"""The ``Agent:`` commit trailer: parsed, added by the commit-msg hook, never blocking."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from review_helpers import run

from devloop import agent


@pytest.mark.parametrize(
    ("message", "tool"),
    [
        ("feat: x\n\nBody.\n\nAgent: codex/gpt-5\n", "codex"),
        ("feat: x\n\nAgent: claude-code/opus-5.5\nCo-authored-by: a <a@b>\n", "claude-code"),
        ("feat: x\n\nAgent: codex/gpt-5\n\nMore text after the trailer.\n", None),
        ("feat: x\n\nAgent: not valid at all\n", None),
        ("feat: x\n", None),
        ("", None),
    ],
    ids=["trailer", "with-others", "not-last-paragraph", "invalid", "none", "empty"],
)
def test_the_tool_comes_from_a_valid_trailer_in_the_last_paragraph(
    message: str, tool: str | None
) -> None:
    assert agent.agent_of(message) == tool


def test_the_trailer_is_added_once_and_only_when_valid(tmp_path: Path) -> None:
    msg = tmp_path / "MSG"
    msg.write_text("feat: x\n\nBody.\n")
    assert agent.add_trailer(msg, "codex/gpt-5")
    assert msg.read_text().endswith("\nAgent: codex/gpt-5\n")
    assert not agent.add_trailer(msg, "claude-code/opus")
    assert msg.read_text().count("Agent:") == 1
    assert not agent.add_trailer(msg, "no slash")


@pytest.fixture()
def clone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Return a repository with a fake ``devloop`` on PATH and the hook installed."""
    run(tmp_path, "init", "-q", "-b", "main")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "devloop"
    fake.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m devloop.cli "$@"\n')
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    done = subprocess.run(
        [str(fake), "agent", "--install"], cwd=tmp_path, capture_output=True, text=True
    )
    assert done.returncode == 0, done.stderr
    return tmp_path


def _commit(repo: Path, env: dict[str, str]) -> str:
    """Commit one more change with ``env`` and return the message git recorded."""
    count = repo / "count.txt"
    count.write_text(str(int(count.read_text()) + 1 if count.exists() else 0))
    run(repo, "add", "-A")
    full = {
        **os.environ,
        **env,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@e.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@e.invalid",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    done = subprocess.run(
        ["git", "commit", "-q", "-m", "feat: x"], cwd=repo, env=full, capture_output=True, text=True
    )
    assert done.returncode == 0, done.stdout + done.stderr
    return run(repo, "log", "-1", "--format=%B")


def test_the_hook_adds_the_trailer_from_devloop_agent(
    clone: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", os.environ["PATH"])
    assert _commit(clone, {"DEVLOOP_AGENT": "codex/gpt-5"}).endswith("Agent: codex/gpt-5")
    assert "Agent:" not in _commit(clone, {"DEVLOOP_AGENT": ""})
    assert "Agent:" not in _commit(clone, {"DEVLOOP_AGENT": "bad value"})  # never blocks


def test_another_tools_hook_is_never_replaced(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(tmp_path, "init", "-q")
    hook = tmp_path / ".git" / "hooks" / "commit-msg"
    hook.write_text("#!/bin/sh\nexit 0\n")
    cwd = Path.cwd()
    os.chdir(tmp_path)
    try:
        assert agent.install() == 2
    finally:
        os.chdir(cwd)
    assert "another commit-msg hook" in capsys.readouterr().err
    assert hook.read_text() == "#!/bin/sh\nexit 0\n"


def test_an_invalid_trailer_already_there_is_never_reported_as_added(tmp_path: Path) -> None:
    msg = tmp_path / "MSG"
    msg.write_text("feat: x\n\nAgent: not valid\n")
    assert not agent.add_trailer(msg, "codex/gpt-5")
    assert msg.read_text() == "feat: x\n\nAgent: not valid\n"


def test_pre_commits_legacy_hook_from_another_tool_is_never_replaced(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from devloop.pr_open import PRE_COMMIT_MARKER

    run(tmp_path, "init", "-q")
    hooks = tmp_path / ".git" / "hooks"
    (hooks / "commit-msg").write_text(f"#!/bin/sh\n{PRE_COMMIT_MARKER}\n")
    (hooks / "commit-msg.legacy").write_text("#!/bin/sh\necho theirs\n")
    cwd = Path.cwd()
    os.chdir(tmp_path)
    try:
        assert agent.install() == 2
    finally:
        os.chdir(cwd)
    assert (hooks / "commit-msg.legacy").read_text() == "#!/bin/sh\necho theirs\n"

"""Tests for ``devloop doctor``, ``devloop init`` and the command line."""

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from devloop.cli import main
from devloop.doctor import diagnose
from devloop.init import init

FIVE = ("claude", "codex", "gemini", "cursor", "copilot")


def _git_repo(path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def test_init_on_an_empty_repo_is_doctor_clean_for_all_five_tools(tmp_path: Path) -> None:
    root = _git_repo(tmp_path / "repo")
    assert main(["init", "--root", str(root)]) == 0
    assert diagnose(root) == []
    assert main(["doctor", "--check", "--root", str(root)]) == 0
    text = (root / "devloop.toml").read_text(encoding="utf-8")
    for tool in FIVE:
        assert f'"{tool}"' in text


def test_init_never_overwrites_an_existing_config(tmp_path: Path) -> None:
    (tmp_path / "devloop.toml").write_text(
        '[framework]\nversion = "0.1"\n[agents]\ntools = ["codex"]\n'
        'skills_targets = [".agents/skills"]\n',
        encoding="utf-8",
    )
    init(tmp_path)
    assert "codex" in (tmp_path / "devloop.toml").read_text(encoding="utf-8")
    assert not (tmp_path / "CLAUDE.md").exists()
    assert diagnose(tmp_path) == []


def test_doctor_without_a_config_says_to_run_init(tmp_path: Path) -> None:
    problems = diagnose(tmp_path)
    assert any("devloop init" in p for p in problems)
    assert main(["doctor", "--check", "--root", str(tmp_path)]) == 1


@pytest.mark.parametrize(
    ("break_it", "expected"),
    [
        (lambda r: (r / ".claude/skills/devloop-setup/SKILL.md").write_text("x"), "drift"),
        (lambda r: (r / "CLAUDE.md").write_text("no import\n"), "CLAUDE.md"),
        (lambda r: (r / ".gemini/settings.json").write_text("{}"), "settings.json"),
        (lambda r: (r / "AGENTS.md").write_text("# AGENTS.md\n"), "About this project"),
        (lambda r: (r / "AGENTS.md").unlink(), "AGENTS.md"),
        (
            lambda r: (r / "devloop.toml").write_text('[framework]\nversion = "9.9"\n'),
            "9.9",
        ),
        (lambda r: (r / "devloop.toml").write_text("[framework]\n"), "framework.version"),
    ],
)
def test_doctor_names_each_kind_of_drift(
    tmp_path: Path, break_it: Callable[[Path], object], expected: str
) -> None:
    init(tmp_path)
    break_it(tmp_path)
    problems = diagnose(tmp_path)
    assert any(expected in p for p in problems), problems
    assert main(["doctor", "--check", "--root", str(tmp_path)]) == 1


def test_doctor_reports_a_tool_with_no_skills_folder_it_reads(tmp_path: Path) -> None:
    (tmp_path / "devloop.toml").write_text(
        '[framework]\nversion = "0.1"\n[agents]\ntools = ["claude", "copilot"]\n'
        'skills_targets = [".claude/skills"]\n',
        encoding="utf-8",
    )
    init(tmp_path)
    problems = diagnose(tmp_path)
    assert any("GitHub Copilot" in p and ".github/skills" in p for p in problems), problems


def test_doctor_without_check_reports_but_exits_zero(tmp_path: Path) -> None:
    assert main(["doctor", "--root", str(tmp_path)]) == 0


def test_sync_command_repairs_drift(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    init(tmp_path)
    (tmp_path / "CLAUDE.md").unlink()
    assert main(["sync", "--root", str(tmp_path)]) == 0
    assert "create CLAUDE.md" in capsys.readouterr().out
    assert diagnose(tmp_path) == []


def test_sync_command_reports_an_error_without_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["sync", "--root", str(tmp_path)]) == 2
    assert "devloop init" in capsys.readouterr().err


@pytest.mark.parametrize("name", ["AGENTS.md", "CLAUDE.md", "devloop.toml"])
@pytest.mark.parametrize("argv", [["sync"], ["doctor", "--check"]])
def test_a_non_utf8_file_is_a_message_not_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str, argv: list[str]
) -> None:
    init(tmp_path)
    (tmp_path / name).write_bytes("@AGENTS.md\n".encode("utf-16"))
    assert main([*argv, "--root", str(tmp_path)]) in {1, 2}
    out = capsys.readouterr()
    assert "UTF-8" in out.out + out.err

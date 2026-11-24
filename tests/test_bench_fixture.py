"""Tests for the conformance benchmark's fixture project (devloop.bench.fixture)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from conftest import git

from devloop.bench.fixture import materialize


def test_the_fixture_starts_clean_with_its_seeded_bug(tmp_path: Path) -> None:
    """Tests pass on the fixture as shipped, the layout is devloop's, and main is committed."""
    root = tmp_path / "work"
    base = materialize(root, "claude")
    assert git(root, "rev-parse", "HEAD").strip() == base
    assert git(root, "branch", "--show-current").strip() == "main"
    assert git(root, "status", "--porcelain") == ""
    assert (root / "AGENTS.md").is_file() and (root / ".claude" / "skills").is_dir()
    assert "devloop:begin rules" in (root / "AGENTS.md").read_text(encoding="utf-8")
    done = subprocess.run(
        [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-t", "."],
        cwd=root,
        capture_output=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert "if entry.cents > 0" in (root / "ledger" / "core.py").read_text(encoding="utf-8")


def test_the_fixture_commit_ignores_signing_and_global_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A user's commit signing and global hooks folder never touch the fixture's commit."""
    home = tmp_path / "home"
    hooks = home / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "pre-commit").write_text("#!/bin/sh\nexit 1\n")
    (hooks / "pre-commit").chmod(0o755)
    (home / ".gitconfig").write_text(
        f"[commit]\n\tgpgsign = true\n[gpg]\n\tprogram = /nonexistent/gpg\n"
        f"[core]\n\thooksPath = {hooks}\n"
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    base = materialize(tmp_path / "work", "claude")
    assert git(tmp_path / "work", "rev-parse", "HEAD").strip() == base


def test_the_fixture_s_sections_replace_only_the_placeholders(tmp_path: Path) -> None:
    """Every managed block survives; the placeholders are gone."""
    root = tmp_path / "work"
    materialize(root, "claude")
    text = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert "What the project is, who uses it" not in text
    assert "A tiny ledger" in text
    assert text.count("devloop:begin") == text.count("devloop:end") >= 1

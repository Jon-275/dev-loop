"""Tests for the role library devloop ships and ``devloop council add``."""

from pathlib import Path

import pytest

from devloop.config import Config
from devloop.council import ALWAYS, CouncilError, add, library, main, plan, problems


def test_every_library_card_is_valid_and_the_always_present_roles_exist() -> None:
    cards = library()
    assert set(ALWAYS) <= set(cards)
    assert len(cards) >= 15
    for name in ALWAYS:
        assert cards[name].summoned_by == ("**",)


def test_the_whole_library_fits_the_default_reviewer_budget() -> None:
    cards = library()
    budget = Config.default("0.4").council.max_reviewers
    for tier, limit in budget.items():
        assert len({c.reviewer for c in cards.values() if tier in c.tiers}) <= limit
    assert plan(cards, ["src/x.py"], tier=2).reviewers.keys() == {"core", "security"}


def test_add_copies_library_cards_and_never_replaces_one(tmp_path: Path) -> None:
    (tmp_path / "devloop.toml").write_text('[framework]\nversion = "0.4"\n', encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x\n", encoding="utf-8")
    assert add(tmp_path, ["architect", "security"]) == [
        "devloop/council/architect.md",
        "devloop/council/security.md",
    ]
    assert problems(tmp_path) == []
    with pytest.raises(CouncilError, match="security.md exists"):
        add(tmp_path, ["editor", "security"])
    assert not (tmp_path / "devloop" / "council" / "editor.md").exists()
    with pytest.raises(CouncilError, match="no library role 'nope'"):
        add(tmp_path, ["nope"])


def test_library_and_add_commands(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["library"]) == 0
    out = capsys.readouterr().out
    assert "always  architect  Principal Architect\n" in out
    assert "pack    payments  Payments Engineer\n" in out
    (tmp_path / "devloop.toml").write_text('[framework]\nversion = "0.4"\n', encoding="utf-8")
    assert main(["add", "--root", str(tmp_path), "payments"]) == 0
    assert "created devloop/council/payments.md" in capsys.readouterr().out
    assert main(["add", "--root", str(tmp_path), "payments"]) == 2

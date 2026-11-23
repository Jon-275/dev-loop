"""``devloop lessons``: accepted findings ranked into bug classes, the page and the rules."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from devloop import lessons as ls
from devloop.cli import main
from devloop.config import Lessons

SEED: dict[str, Any] = {
    "source": "council",
    "classes": [
        {
            "id": "swallowed",
            "title": "Swallowed errors",
            "rule": "Re-raise.",
            "bad": "x",
            "good": "y",
            "keywords": ["swallowed", "broad except"],
            "prs": [3],
        },
        {
            "id": "races",
            "title": "Races",
            "rule": "Lock it.",
            "bad": "x",
            "good": "y",
            "keywords": ["race"],
            "prs": [],
        },
    ],
}


def _record(title: str, outcome: str = "accepted", pr: int = 9) -> dict[str, Any]:
    """Return a feedback store record."""
    return {"reviewer": "Gemini", "title": title, "outcome": outcome, "pr": pr}


def test_keywords_match_whole_words_only() -> None:
    [_, races] = ls.seed_classes(SEED)
    assert races.matches("A race on promote") and not races.matches("stack trace dropped")


def test_accepted_findings_count_rejected_never_and_unmatched_are_kept() -> None:
    classes = ls.seed_classes(SEED)
    records = [_record("Error swallowed in sync"), _record("A race", "rejected"), _record("Typo")]
    unmatched = ls.tally(classes, records)
    swallowed, races = classes
    assert (swallowed.seen, swallowed.prs, races.seen) == (2, {3, 9}, 0)
    assert [r["title"] for r in unmatched] == ["Typo"]
    assert [c.id for c in ls.ranked(classes)] == ["swallowed", "races"]


def test_council_notes_count_too(tmp_path: Path) -> None:
    (tmp_path / "review-pr12.md").write_text("### P1-1 A race between the writers\n")
    classes = ls.seed_classes(SEED)
    ls.tally(classes, [], ls.note_findings(tmp_path))
    assert (classes[1].seen, classes[1].prs) == (1, {12})
    assert ls.note_findings(tmp_path / "missing") == []


def test_the_outputs_are_deterministic_and_the_rules_block_is_replaced() -> None:
    agents = "# Agents\n\n## Repository Structure\n"
    settings = Lessons(repo="o/r")
    first = ls.render(SEED, {"records": [_record("swallowed")]}, agents, settings=settings)
    assert first == ls.render(SEED, {"records": [_record("swallowed")]}, agents, settings=settings)
    assert first.agents.index(ls.START) < first.agents.index("## Repository Structure")
    assert "1. **Swallowed errors.** Re-raise. (seen 2 times)" in first.agents
    again = ls.render(SEED, {}, first.agents, settings=settings)
    assert again.agents.count(ls.START) == 1 and "(seen 1 time)" in again.agents
    assert "[#3](https://github.com/o/r/pull/3)" in first.doc
    assert "PRs: #3." in ls.render(SEED, {}, agents).doc
    assert json.loads(first.patterns)["classes"][0] == {
        "id": "swallowed",
        "title": "Swallowed errors",
        "rule": "Re-raise.",
        "seen": 2,
    }


def test_an_input_that_exists_but_cannot_be_read_fails_instead_of_reading_empty(
    tmp_path: Path,
) -> None:
    assert ls.load_json(tmp_path / "none.json") == {}
    (tmp_path / "bad.json").write_text("[1]")
    with pytest.raises(ls.LessonsError, match="not a JSON object"):
        ls.load_json(tmp_path / "bad.json")


def test_the_command_writes_the_three_outputs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "devloop.toml").write_text(
        '[framework]\nversion = "0.3"\n[review]\n'
        'feedback = "data/fb.json"\nlessons = "data/lessons.json"\n'
    )
    (tmp_path / "devloop").mkdir()
    (tmp_path / "devloop" / "lessons_seed.json").write_text(json.dumps(SEED))
    (tmp_path / "AGENTS.md").write_text("# Agents\n")
    assert main(["lessons", "--root", str(tmp_path)]) == 0
    assert (tmp_path / "docs" / "lessons-learned.md").read_text().startswith("# Lessons learned")
    assert ls.START in (tmp_path / "AGENTS.md").read_text()
    assert json.loads((tmp_path / "data" / "lessons.json").read_text())["version"] == 1
    (tmp_path / "data" / "fb.json").write_text("{broken")
    before = (tmp_path / "AGENTS.md").read_text()
    assert main(["lessons", "--root", str(tmp_path)]) == 2
    assert "nothing written" in capsys.readouterr().err
    assert (tmp_path / "AGENTS.md").read_text() == before

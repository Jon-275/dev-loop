"""The replay script's comparison, and that its public report never shows private text."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("replay", ROOT / "scripts" / "replay.py")
assert _spec is not None and _spec.loader is not None
replay = importlib.util.module_from_spec(_spec)
sys.modules["replay"] = replay
_spec.loader.exec_module(replay)


def _side(prompt: str, *places: tuple[str, int], error: str = "") -> object:
    """Return a side that sent ``prompt``, posted one finding per place, and ended in ``error``."""
    side = replay.Side(prompts=[("high", prompt)], log="Posted.", error=error)
    side.comments = [{"path": p, "line": n, "body": "**P2 — Secret title**\nx"} for p, n in places]
    return side


def test_findings_match_by_severity_file_and_nearby_line() -> None:
    ours = [("P2", "a.py", 10, "x"), ("P1", "b.py", 3, "y")]
    theirs = [("P2", "a.py", 14, "other words"), ("P2", "b.py", 3, "y")]
    assert replay.matched(ours, theirs) == 1
    assert replay.matched(theirs, ours) == 1


def test_identical_prompts_pass_and_different_ones_fail() -> None:
    same, _ = replay.compare("gemini", _side("P"), _side("P"))
    different, lines = replay.compare("gemini", _side("P"), _side("Q"))
    assert same and not different
    assert any(line.startswith("```diff") for line in lines)


def test_a_public_report_holds_no_prompt_path_or_title() -> None:
    gtm = _side("SECRET PROMPT", ("private/app.py", 3))
    dev = _side(
        "OTHER SECRET", ("private/app.py", 4), error="ReviewError: private/app.py was not reviewed"
    )
    _, lines = replay.compare("gemini", gtm, dev, public=True)
    text = "\n".join(lines)
    assert "SECRET" not in text and "private/app.py" not in text and "Secret title" not in text
    assert "1 of gtm_toolkit's have a devloop finding" in text
    assert "devloop failed: `ReviewError`" in text
    assert "Posted." not in text  # a side's log line stays out of a public report


def test_a_comment_without_a_heading_or_line_is_not_a_finding() -> None:
    side = replay.Side(
        comments=[
            {"path": "a.py", "line": None, "body": "x"},
            {"path": "a.py"},
            {"line": 3, "body": "x"},
        ]
    )
    assert replay.findings(side) == []


def test_an_unknown_reviewer_is_an_argument_error(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(SystemExit):
        replay.main(
            ["--gtm", str(tmp_path), "--base", "x", "--out", str(tmp_path), "--providers", " gpt"]
        )

"""Tests for AGENTS.md managed blocks: upgrades rewrite them, project text is never touched."""

import pytest

from devloop.agents_md import (
    BlockError,
    apply_blocks,
    missing_sections,
    parse_blocks,
    render_template,
)
from devloop.config import Config, parse_config

DOC = """# AGENTS.md

Project intro the framework must never touch.

<!-- devloop:begin rules -->
old rules
<!-- devloop:end rules -->

## About this project

Our own words.
"""


def test_parse_blocks_returns_each_body() -> None:
    assert parse_blocks(DOC) == {"rules": "old rules\n"}


def test_apply_blocks_rewrites_managed_text_and_keeps_project_text() -> None:
    out = apply_blocks(DOC, {"rules": "new rules\n"})
    assert parse_blocks(out) == {"rules": "new rules\n"}
    assert out.replace("new rules", "old rules") == DOC


def test_apply_blocks_is_idempotent() -> None:
    once = apply_blocks(DOC, {"rules": "new\n", "review": "r\n"})
    assert apply_blocks(once, {"rules": "new\n", "review": "r\n"}) == once


def test_a_missing_block_is_appended_and_a_retired_one_removed() -> None:
    out = apply_blocks(DOC, {"review": "r\n"})
    assert parse_blocks(out) == {"review": "r\n"}
    assert "Our own words." in out
    assert "old rules" not in out


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("<!-- devloop:begin a -->\nx\n", "never closed"),
        ("<!-- devloop:end a -->\n", "without a begin"),
        ("<!-- devloop:begin a -->\n<!-- devloop:begin b -->\n", "inside"),
        ("<!-- devloop:begin a -->\n<!-- devloop:end b -->\n", "closes"),
        (
            "<!-- devloop:begin a -->\n<!-- devloop:end a -->\n"
            "<!-- devloop:begin a -->\n<!-- devloop:end a -->\n",
            "twice",
        ),
    ],
)
def test_malformed_markers_are_errors_not_guesses(text: str, message: str) -> None:
    with pytest.raises(BlockError, match=message):
        parse_blocks(text)


def test_template_renders_config_values_into_managed_blocks() -> None:
    blocks, doc = render_template(Config.default(version="0.1"))
    assert set(blocks) == {"rules", "review", "coordination"}
    assert "800 changed lines" in blocks["rules"]
    assert "`accepted:`" in blocks["review"]
    assert parse_blocks(doc) == blocks
    assert missing_sections(doc) == []


def test_missing_sections_names_each_required_project_heading() -> None:
    assert missing_sections("# AGENTS.md\n## Commands\n") == ["## About this project"]


def test_markers_inside_code_fences_are_examples_not_blocks() -> None:
    doc = (
        "```markdown\n<!-- devloop:begin rules -->\nexample\n<!-- devloop:end rules -->\n```\n"
        + DOC
    )
    assert parse_blocks(doc) == {"rules": "old rules\n"}
    out = apply_blocks(doc, {"rules": "new\n"})
    assert out.startswith("```markdown\n<!-- devloop:begin rules -->\nexample\n")


def test_coordination_block_says_how_to_merge_a_stack() -> None:
    block = render_template(Config.default(version="0.1"))[0]["coordination"]
    assert "merge commit" in block
    assert "gh pr edit <next> --base main" in block
    assert "only when GitHub reports a conflict" in block


def test_coordination_omits_commit_attribution_when_disabled() -> None:
    config = parse_config('[framework]\nversion = "0.10"\n[agents]\ncommit_attribution = false\n')
    block = render_template(config)[0]["coordination"]
    assert "DEVLOOP_AGENT" not in block
    assert "Agent:" not in block
    assert "merge commit" in block
    assert "normal repository identity" in block


def test_coordination_requires_opt_in_for_commit_attribution() -> None:
    standard = render_template(Config.default(version="0.10"))[0]["coordination"]
    assert "DEVLOOP_AGENT" not in standard
    enabled = parse_config('[framework]\nversion = "0.10"\n[agents]\ncommit_attribution = true\n')
    assert "DEVLOOP_AGENT" in render_template(enabled)[0]["coordination"]


def test_rules_block_asks_for_simple_clear_language() -> None:
    block = render_template(Config.default(version="0.1"))[0]["rules"]
    assert "Keep it simple." in block

"""Tests for the tool registry."""

from devloop.config import Agents
from devloop.tools import TOOLS, default_skills_targets


def test_every_tool_records_its_documentation_sources() -> None:
    for tool in TOOLS.values():
        assert tool.sources, tool.name
        assert all(s.startswith("https://") for s in tool.sources)


def test_tools_that_do_not_read_agents_md_have_an_adapter() -> None:
    for tool in TOOLS.values():
        assert tool.reads_agents_md or tool.adapter, tool.name


def test_default_targets_serve_every_tool_with_the_fewest_folders() -> None:
    targets = default_skills_targets(tuple(TOOLS))
    assert targets == (".claude/skills", ".agents/skills", ".github/skills")
    for tool in TOOLS.values():
        assert set(tool.skills_dirs) & set(targets), tool.name


def test_config_default_targets_match_the_registry() -> None:
    assert Agents().skills_targets == default_skills_targets(Agents().tools)


def test_a_single_tool_needs_only_its_own_folder() -> None:
    assert default_skills_targets(("codex",)) == (".agents/skills",)


def test_antigravity_reads_agents_md_and_the_shared_skills_folder() -> None:
    tool = TOOLS["antigravity"]
    assert tool.reads_agents_md
    assert tool.adapter is None
    assert tool.skills_dirs == (".agents/skills",)


def test_antigravity_needs_no_extra_skills_folder() -> None:
    assert "antigravity" in Agents().tools
    assert default_skills_targets(Agents().tools) == (
        ".claude/skills",
        ".agents/skills",
        ".github/skills",
    )

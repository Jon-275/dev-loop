"""Tests that every skill devloop ships is portable and renders cleanly for every tool.

The rules for shipped skills (framework plan, section 8): only portable frontmatter, no
tool-specific tool names in the body, commands only through ``devloop`` (and git or gh),
every ``devloop`` command and flag a skill names really exists, and every skill says when
not to use it.
"""

import re
from pathlib import Path

import pytest

from devloop import cli
from devloop.config import DEFAULT_SKILLS, Skills, load_config
from devloop.doctor import diagnose
from devloop.init import init
from devloop.skills import framework_skills
from devloop.tools import TOOLS

SKILLS = framework_skills()
#: Names that tie a skill to one agent: its own tool names, or a product's name.
TOOL_SPECIFIC = re.compile(
    r"\b(?:(?:Bash|Read|Edit|Write|Grep|Glob|Task|WebFetch|NotebookEdit|TodoWrite)\s+tool"
    r"|TodoWrite|SendMessage|subagent_type|Claude Code|Codex|Cursor|Copilot|Gemini CLI"
    r"|Antigravity|CLAUDE\.md)\b"
)
#: A ``devloop`` command inside backticks, with the flags written after it.
COMMAND = re.compile(r"`devloop ([a-z][a-z-]*)((?: [^`]*)?)`")


def _body(name: str) -> str:
    return SKILLS[name].files["SKILL.md"].decode("utf-8")


def _help(command: str, capsys: pytest.CaptureFixture[str]) -> str:
    with pytest.raises(SystemExit) as done:
        cli.main([command, "--help"])
    assert done.value.code == 0, f"devloop {command} --help failed"
    return capsys.readouterr().out


def test_the_default_skills_are_the_shipped_skills() -> None:
    assert set(DEFAULT_SKILLS) == set(SKILLS)
    assert Skills().include == DEFAULT_SKILLS


@pytest.mark.parametrize("name", sorted(SKILLS))
def test_a_shipped_skill_says_when_not_to_use_it(name: str) -> None:
    body = _body(name)
    assert "\n## When not to use\n" in body, f"{name}: add a '## When not to use' section"
    assert re.search(r"\bnot (?:for|when|to)\b", SKILLS[name].description), (
        f"{name}: the description must say when not to use the skill"
    )


@pytest.mark.parametrize("name", sorted(SKILLS))
def test_a_shipped_skill_names_no_agent_specific_tool(name: str) -> None:
    found = TOOL_SPECIFIC.findall(_body(name))
    assert not found, f"{name} names {found}; say it in terms every agent understands"


@pytest.mark.parametrize("name", sorted(SKILLS))
def test_every_devloop_command_a_skill_names_exists(
    name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    for command, rest in COMMAND.findall(_body(name)):
        text = _help(command, capsys)
        for flag in re.findall(r"--[a-z][a-z-]*", rest):
            assert flag in text, f"{name}: `devloop {command}` has no {flag}"


def test_every_shipped_skill_renders_for_every_tool_and_passes_doctor(tmp_path: Path) -> None:
    init(tmp_path)
    config = load_config(tmp_path / "devloop.toml")
    assert set(config.agents.tools) == set(TOOLS)
    for target in config.agents.skills_targets:
        for name, skill in SKILLS.items():
            for rel, content in skill.files.items():
                assert (tmp_path / target / name / rel).read_bytes() == content
    assert diagnose(tmp_path) == []

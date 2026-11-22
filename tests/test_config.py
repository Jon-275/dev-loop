"""Tests for loading and validating devloop.toml."""

from pathlib import Path

import pytest

from devloop.config import Config, ConfigError, ExcludeBlock, load_config, parse_config

FULL = """
[framework]
version = "0.1"

[pr]
max_lines = 800
exclude = ["uv.lock", "data/*.json"]
exclude_blocks = [{ path = "AGENTS.md", start = "<!-- a -->", end = "<!-- b -->" }]
large_label = "large-pr-approved"

[tiers]
1 = ["src/**/crm_*.py"]
3 = ["docs/**", "*.md"]

[review]
providers = { 1 = ["greptile", "gemini"], 2 = ["greptile"], 3 = ["greptile"] }
blocking_severities = ["P1", "P2"]
lenses_dir = "devloop/lenses"

[labels]
prefixes = ["accepted", "rejected"]

[agents]
tools = ["claude", "codex", "gemini", "cursor", "copilot"]
skills_targets = [".agents/skills", ".claude/skills"]

[skills]
include = ["devloop-setup"]
project = "devloop/skills"

[council]
roles_dir = "devloop/council"
max_reviewers = { 1 = 5, 2 = 2 }
require_status = [1]
"""


def test_full_config_round_trips_every_section() -> None:
    cfg = parse_config(FULL)
    assert cfg.framework.version == "0.1"
    assert cfg.pr.max_lines == 800
    assert cfg.pr.exclude == ("uv.lock", "data/*.json")
    assert cfg.pr.exclude_blocks == (ExcludeBlock("AGENTS.md", "<!-- a -->", "<!-- b -->"),)
    assert cfg.tiers == {1: ("src/**/crm_*.py",), 3: ("docs/**", "*.md")}
    assert cfg.review.providers[1] == ("greptile", "gemini")
    assert cfg.review.blocking_severities == ("P1", "P2")
    assert cfg.labels.prefixes == ("accepted", "rejected")
    assert cfg.agents.tools == ("claude", "codex", "gemini", "cursor", "copilot")
    assert cfg.agents.skills_targets == (".agents/skills", ".claude/skills")
    assert cfg.skills.include == ("devloop-setup",)
    assert cfg.council.max_reviewers == {1: 5, 2: 2}
    assert cfg.council.require_status == (1,)


def test_missing_sections_take_defaults() -> None:
    cfg = parse_config('[framework]\nversion = "0.1"\n')
    assert cfg == Config.default(version="0.1")
    assert cfg.pr.max_lines == 800


def test_framework_version_is_required() -> None:
    with pytest.raises(ConfigError, match=r"framework\.version"):
        parse_config("[pr]\nmax_lines = 800\n")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('[framework]\nversion = "0.1"\nbogus = 1\n', r"unknown key.*framework\.bogus"),
        ('[framework]\nversion = "0.1"\n[nope]\n', r"unknown section.*nope"),
        ('[framework]\nversion = "0.1"\n[pr]\nmax_lines = 0\n', r"pr\.max_lines"),
        ('[framework]\nversion = "0.1"\n[pr]\nmax_lines = "800"\n', r"pr\.max_lines"),
        ('[framework]\nversion = "0.1"\n[pr]\nmax_lines = true\n', r"pr\.max_lines"),
        ('[framework]\nversion = "0.1"\n[pr]\nexclude = "uv.lock"\n', r"pr\.exclude"),
        ('[framework]\nversion = "0.1"\n[tiers]\n4 = ["x"]\n', r"tiers.*4"),
        ('[framework]\nversion = "0.1"\n[tiers]\n2 = ["x"]\n', r"tier 2.*default"),
        ('[framework]\nversion = "0.1"\n[tiers]\none = ["x"]\n', r"tiers.*one"),
        ('[framework]\nversion = "0.1"\n[review]\nblocking_severities = ["P9"]\n', r"P9"),
        ('[framework]\nversion = "0.1"\n[agents]\ntools = ["vim"]\n', r"unknown tool.*vim"),
        ('[framework]\nversion = "0.1"\n[agents]\ntools = ["claude", "claude"]\n', r"twice"),
        ('[framework]\nversion = "0.1"\n[agents]\nskills_targets = []\n', r"skills_targets"),
        ('[framework]\nversion = "0.1"\n[agents]\nskills_targets = ["../x"]\n', r"inside"),
        ('[framework]\nversion = "0.1"\n[agents]\nskills_targets = ["/abs"]\n', r"inside"),
        ('[framework]\nversion = "0.1"\n[council]\nmax_reviewers = { 1 = -1 }\n', r"max_reviewers"),
        ('[framework]\nversion = "0.1"\n[council]\nrequire_status = [7]\n', r"require_status"),
        ('[framework]\nversion = "0.1"\n[labels]\nprefixes = ["ok:"]\n', r"prefixes"),
        ('[framework]\nversion = "0.1"\n[pr]\nexclude_blocks = "x"\n', r"exclude_blocks"),
        (
            '[framework]\nversion = "0.1"\n[pr]\nexclude_blocks = [{ path = "a" }]\n',
            r"exclude_blocks\[0\]",
        ),
        (
            '[framework]\nversion = "0.1"\n[pr]\n'
            'exclude_blocks = [{ path = "../a", start = "s", end = "e" }]\n',
            r"inside",
        ),
        ("not = [toml", r"not valid TOML"),
    ],
)
def test_invalid_values_are_rejected_with_their_path(text: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_config(text)


def test_load_config_reads_the_file(tmp_path: Path) -> None:
    path = tmp_path / "devloop.toml"
    path.write_text(FULL, encoding="utf-8")
    assert load_config(path).pr.large_label == "large-pr-approved"


def test_load_config_reports_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "devloop.toml")

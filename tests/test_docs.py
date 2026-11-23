"""Tests that the docs stay true to the code: every config key and every skill is listed."""

import dataclasses
import re
from pathlib import Path

from devloop.config import Config, Reviewer
from devloop.skills import framework_skills

DOCS = Path(__file__).resolve().parents[1] / "docs"


def _documented() -> dict[str, set[str]]:
    """Return each ``## [section]`` of the configuration page with the keys its table names."""
    found: dict[str, set[str]] = {}
    section = ""
    for line in (DOCS / "configuration.md").read_text(encoding="utf-8").splitlines():
        heading = re.match(r"^#{2,3} \[([a-z.A-Z]+)\]$", line)
        if heading:
            section = heading.group(1)
            found[section] = set()
        key = re.match(r"^\| `([a-z_0-9]+)` \|", line)
        if key and section:
            found[section].add(key.group(1))
    return found


def _schema() -> dict[str, set[str]]:
    """Return every section of ``devloop.toml`` with its keys, from the config dataclasses."""
    schema: dict[str, set[str]] = {"tiers": {"1", "3"}}
    for section in dataclasses.fields(Config):
        if section.name != "tiers":
            value = getattr(Config.default("0"), section.name)
            schema[section.name] = {f.name for f in dataclasses.fields(value)}
    schema["review.reviewers.NAME"] = {f.name for f in dataclasses.fields(Reviewer)}
    return schema


def test_the_configuration_page_documents_exactly_the_schema() -> None:
    assert _documented() == _schema()


def test_the_skills_page_lists_every_shipped_skill() -> None:
    text = (DOCS / "skills.md").read_text(encoding="utf-8")
    listed = set(re.findall(r"^\| `([a-z0-9-]+)` \|", text, re.MULTILINE))
    assert listed == set(framework_skills())

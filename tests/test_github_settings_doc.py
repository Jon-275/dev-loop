"""Tests that the GitHub settings page is published and the pages that give its advice link it."""

import dataclasses
import re
from pathlib import Path

import yaml

from devloop.config import GitHub

ROOT = Path(__file__).resolve().parents[1]
PAGE = "github-settings.md"


def _page(name: str) -> str:
    """Return the text of the docs page ``name``."""
    return (ROOT / "docs" / name).read_text(encoding="utf-8")


def test_the_page_is_in_the_site_navigation() -> None:
    nav = yaml.safe_load((ROOT / "mkdocs.yml").read_text(encoding="utf-8"))["nav"]
    assert {"GitHub settings": PAGE} in nav


def test_adoption_and_the_security_model_link_the_page() -> None:
    for name in ("adoption.md", "security.md"):
        assert f"]({PAGE}" in _page(name), name


def test_the_baseline_is_one_table_with_a_value_a_reason_and_a_place_for_every_setting() -> None:
    text = _page(PAGE)
    baseline = text.split("## The baseline")[1].split("\n## ")[0]
    rows = [line for line in baseline.splitlines() if line.startswith("|")]
    assert rows[0] == "| Setting | Value | Why | Where it is set (API field) |"
    assert len(rows) >= 20
    for row in rows[2:]:
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        assert len(cells) == 4 and all(cells), row


def test_the_page_says_what_it_leaves_out_and_why() -> None:
    left_out = _page(PAGE).split("## Left out on purpose")[1]
    assert re.findall(r"^- \*\*(.+?)\.\*\*", left_out, re.MULTILINE) == [
        "Required approvals",
        "Signed commits",
        "Linear history",
    ]


def test_the_page_names_every_key_the_command_judges() -> None:
    section = _page(PAGE).split("## Check the settings")[1].split("### Check by hand")[0]
    judged = section.split("These keys are judged:")[1].split(".")[0]
    assert re.findall(r"`([a-z_]+)`", judged) == [f.name for f in dataclasses.fields(GitHub)]
    assert "devloop doctor --repo OWNER/NAME" in section


def test_the_baseline_requires_a_pull_request_as_adoption_does() -> None:
    assert "require a pull request" in _page("adoption.md")
    baseline = _page(PAGE).split("## The baseline")[1].split("\n## ")[0]
    rows = [line for line in baseline.splitlines() if line.startswith("| A pull request before")]
    assert len(rows) == 1
    assert "zero required approvals" in rows[0] and "required_pull_request_reviews" in rows[0]

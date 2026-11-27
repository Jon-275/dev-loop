"""Tests that the front pages list what exists: every shared workflow and every command.

A shared workflow is a file under ``.github/workflows/`` that can be called
(``on: workflow_call``). A command is a subcommand of the ``devloop`` parser. README.md
and the adoption page must name exactly those, so a new workflow or command cannot ship
without its line, and a removed one cannot stay listed.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import yaml

from devloop import cli

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def shared_workflows(folder: Path) -> set[str]:
    """Return the file names of the workflows in ``folder`` that another workflow can call."""
    found = set()
    for path in sorted(folder.glob("*.yml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        # YAML 1.1 reads the key ``on`` as the boolean true.
        triggers = data.get("on", data.get(True))
        names = triggers if isinstance(triggers, (dict, list)) else [triggers]
        if "workflow_call" in names:
            found.add(path.name)
    return found


def commands() -> set[str]:
    """Return the name of every ``devloop`` subcommand, from the parser itself."""
    for action in cli._parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices)
    raise AssertionError("the devloop parser has no subcommands")


def section(text: str, heading: str) -> str:
    """Return the text under the ``## heading`` of a Markdown page, up to the next ``## ``."""
    match = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert match, f"no '## {heading}' section"
    return match.group(1)


def readme_workflows(text: str) -> set[str]:
    """Return the workflows README's "In CI" list names, one per list item."""
    return set(re.findall(r"^- \[`([\w-]+\.yml)`\]", section(text, "In CI"), re.M))


def readme_commands(text: str) -> set[str]:
    """Return every ``devloop <command>`` README's "Commands" section names."""
    return set(re.findall(r"`devloop ([a-z][a-z-]*)", section(text, "Commands")))


def adoption_workflows(text: str) -> set[str]:
    """Return the workflows in the first column of the adoption page's table."""
    return set(re.findall(r"^\| `([\w-]+\.yml)` \|", text, re.M))


def test_a_workflow_is_shared_only_when_it_can_be_called(tmp_path: Path) -> None:
    (tmp_path / "called.yml").write_text("on:\n  workflow_call:\n    inputs: {}\njobs: {}\n")
    (tmp_path / "listed.yml").write_text("on: [push, workflow_call]\njobs: {}\n")
    (tmp_path / "caller.yml").write_text("on:\n  pull_request:\njobs: {}\n")
    (tmp_path / "single.yml").write_text("on: push\njobs: {}\n")
    assert shared_workflows(tmp_path) == {"called.yml", "listed.yml"}


def test_the_readers_find_what_a_page_names() -> None:
    page = (
        "## Commands\n\n- `devloop init`: set up.\n- `devloop size --base X`, `devloop pr-open`.\n"
        "\n## In CI\n\n- [`a-b.yml`](x): `devloop gate`.\n- see [`ci.yml`](y)\n\n## Other\n"
        "\n`devloop sync`\n"
    )
    assert readme_commands(page) == {"init", "size", "pr-open"}
    assert readme_workflows(page) == {"a-b.yml"}
    assert adoption_workflows("| `a.yml` | x |\n| `b` | `c.yml` |\n") == {"a.yml"}


def test_readme_lists_exactly_the_shared_workflows() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert readme_workflows(text) == shared_workflows(WORKFLOWS)


def test_readme_lists_exactly_the_commands() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert readme_commands(text) == commands()


def test_the_adoption_page_lists_exactly_the_shared_workflows() -> None:
    text = (ROOT / "docs" / "adoption.md").read_text(encoding="utf-8")
    assert adoption_workflows(text) == shared_workflows(WORKFLOWS)

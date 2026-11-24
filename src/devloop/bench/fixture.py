"""Copy the benchmark's fixture project to a scratch directory, ready for one tool."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from devloop import __version__
from devloop.config import CONFIG_FILE, load_config
from devloop.git import git
from devloop.init import init
from devloop.sync import apply

SOURCE = Path(__file__).resolve().parent / "project"
#: The fixture's first commit: a fixed identity, never signed, and none of the user's
#: global hooks. The agent's own commits use the user's git settings.
IDENTITY = (
    "-c",
    "user.name=devloop bench",
    "-c",
    "user.email=bench@devloop.invalid",
    "-c",
    "commit.gpgsign=false",
    "-c",
    f"core.hooksPath={os.devnull}",
)
#: The template's placeholder sections, which the fixture's own replace.
PLACEHOLDERS = """## About this project

What the project is, who uses it, and what must never break.

## Commands

How to install, test, lint and type-check. Every agent runs these before declaring done.
"""

ABOUT = """## About this project

A tiny ledger (`ledger/`): entries with an amount in whole cents, their total, and the
JSON records it reads (`data/`). Amounts are exact cents and are never rounded. Records
written by earlier versions must keep loading.

## Commands

```sh
python -m unittest discover -s tests -t .   # the tests; run them before declaring done
devloop changelog check                     # changelog fragments in changes/
devloop size --base main                    # the branch's size against pr.max_lines
```
"""


def config(tool: str) -> str:
    """Return the fixture's ``devloop.toml``, with only ``tool`` as the agent in use."""
    version = ".".join(__version__.split(".")[:2])
    return f"""[framework]
version = "{version}"

[pr]
max_lines = 200

[agents]
tools = ["{tool}"]

[changelog]
code = ["ledger/*"]
"""


def _fill_sections(agents_md: Path) -> None:
    """Replace the template's placeholder sections with the fixture's own."""
    text = agents_md.read_text(encoding="utf-8")
    if PLACEHOLDERS not in text:
        raise ValueError(f"{agents_md}: the template's placeholder sections are not there")
    agents_md.write_text(text.replace(PLACEHOLDERS, ABOUT), encoding="utf-8")


def materialize(dest: Path, tool: str) -> str:
    """Write the fixture to ``dest`` for ``tool``, commit it on main, and return the commit.

    ``dest`` must not exist. The layout (``AGENTS.md``, skills, adapters) is rendered by
    devloop itself, so the agent reads exactly what an adopting project would.
    """
    shutil.copytree(SOURCE, dest, ignore=shutil.ignore_patterns("__pycache__"))
    (dest / "gitignore").rename(dest / ".gitignore")
    (dest / CONFIG_FILE).write_text(config(tool), encoding="utf-8")
    init(dest)
    _fill_sections(dest / "AGENTS.md")
    apply(dest, load_config(dest / CONFIG_FILE))
    git(dest, "init", "-q", "-b", "main")
    git(dest, "add", "-A")
    git(dest, *IDENTITY, "commit", "-q", "-m", "chore: the fixture project")
    return git(dest, "rev-parse", "HEAD").strip()

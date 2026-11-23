"""``devloop init``: give a project a ``devloop.toml`` and render everything from it."""

from __future__ import annotations

from pathlib import Path

from devloop import __version__
from devloop.config import CONFIG_FILE, DEFAULT_SKILLS, Agents, load_config
from devloop.sync import Change, apply


def _quoted(values: tuple[str, ...]) -> str:
    """Render ``values`` as a TOML array of strings."""
    return "[" + ", ".join(f'"{v}"' for v in values) + "]"


def starter_config() -> str:
    """Return the ``devloop.toml`` a new project starts with."""
    version = ".".join(__version__.split(".")[:2])
    agents = Agents()
    return f"""# devloop configuration: the only place this project differs from the framework.

[framework]
version = "{version}"

[pr]
max_lines = 800
exclude = ["uv.lock"]
large_label = "large-pr-approved"

[tiers]                # riskiest matching file sets a PR's tier; everything else is tier 2
3 = ["docs/**", "*.md"]

[agents]
tools = {_quoted(agents.tools)}
skills_targets = {_quoted(agents.skills_targets)}

[skills]
include = {_quoted(DEFAULT_SKILLS)}
project = "devloop/skills"
"""


def init(root: Path) -> list[Change]:
    """Write ``devloop.toml`` if absent, then render AGENTS.md, skills and adapters."""
    path = root / CONFIG_FILE
    created: list[Change] = []
    if not path.exists():
        root.mkdir(parents=True, exist_ok=True)
        path.write_text(starter_config(), encoding="utf-8")
        created.append(Change(CONFIG_FILE, "create"))
    return created + apply(root, load_config(path))

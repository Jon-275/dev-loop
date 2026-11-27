"""The version is written in two places that a release must change together."""

import tomllib
from pathlib import Path

from devloop import __version__

ROOT = Path(__file__).resolve().parents[1]


def test_the_package_and_its_metadata_give_the_same_version() -> None:
    """``devloop doctor`` compares with ``__version__``; an install reports the metadata."""
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert metadata["project"]["version"] == __version__


def test_this_repository_s_own_settings_name_that_minor_version() -> None:
    """The ``framework.version`` here moves with each release, since devloop uses itself."""
    settings = tomllib.loads((ROOT / "devloop.toml").read_text(encoding="utf-8"))
    assert settings["framework"]["version"] == ".".join(__version__.split(".")[:2])

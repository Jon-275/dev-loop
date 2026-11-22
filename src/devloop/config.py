"""The ``devloop.toml`` schema: load, validate and default every section.

The file is the only place a project differs from the framework. Validation is strict:
unknown sections and keys are errors, so a typo never silently falls back to a default.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from devloop.tools import TOOLS

CONFIG_FILE = "devloop.toml"
SEVERITIES = ("P0", "P1", "P2", "P3")
TIERS = (1, 2, 3)
_LABEL = re.compile(r"^[a-z][a-z0-9-]*$")


class ConfigError(ValueError):
    """Raised when ``devloop.toml`` is missing, malformed or holds an invalid value."""


@dataclass(frozen=True)
class Framework:
    """``[framework]``: the framework version the project's templates are pinned to."""

    version: str


@dataclass(frozen=True)
class PR:
    """``[pr]``: size limit, paths left out of the count, and the label that waives it."""

    max_lines: int = 800
    exclude: tuple[str, ...] = ()
    large_label: str = "large-pr-approved"


@dataclass(frozen=True)
class Review:
    """``[review]``: reviewer providers per tier and which severities block a merge."""

    providers: dict[int, tuple[str, ...]] = field(default_factory=dict)
    blocking_severities: tuple[str, ...] = ("P0", "P1", "P2")
    lenses_dir: str = "devloop/lenses"


@dataclass(frozen=True)
class Labels:
    """``[labels]``: the prefixes a review-thread reply must start with."""

    prefixes: tuple[str, ...] = ("accepted", "rejected")


@dataclass(frozen=True)
class Agents:
    """``[agents]``: the coding agents in use and where rendered skills go."""

    tools: tuple[str, ...] = ("claude", "codex", "gemini", "cursor", "copilot")
    skills_targets: tuple[str, ...] = (".claude/skills", ".agents/skills", ".github/skills")


@dataclass(frozen=True)
class Skills:
    """``[skills]``: framework skills to render, and the project's own skills folder."""

    include: tuple[str, ...] = ("devloop-setup",)
    project: str = "devloop/skills"


@dataclass(frozen=True)
class Council:
    """``[council]``: where role cards live, reviewer budget per tier, required status."""

    roles_dir: str = "devloop/council"
    max_reviewers: dict[int, int] = field(default_factory=lambda: {1: 5, 2: 2})
    require_status: tuple[int, ...] = (1,)


@dataclass(frozen=True)
class Config:
    """A validated ``devloop.toml``."""

    framework: Framework
    pr: PR = field(default_factory=PR)
    tiers: dict[int, tuple[str, ...]] = field(default_factory=dict)
    review: Review = field(default_factory=Review)
    labels: Labels = field(default_factory=Labels)
    agents: Agents = field(default_factory=Agents)
    skills: Skills = field(default_factory=Skills)
    council: Council = field(default_factory=Council)

    @classmethod
    def default(cls, version: str) -> Config:
        """Return the configuration a project gets when it sets only the version."""
        return cls(framework=Framework(version=version))


def load_config(path: Path) -> Config:
    """Read and validate the config file at ``path``."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"{path} not found: run `devloop init`") from exc
    return parse_config(text)


def parse_config(text: str) -> Config:
    """Parse and validate the TOML ``text`` of a ``devloop.toml``."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{CONFIG_FILE} is not valid TOML: {exc}") from exc
    sections = {"framework", "pr", "tiers", "review", "labels", "agents", "skills", "council"}
    for name in data:
        if name not in sections:
            raise ConfigError(f"unknown section [{name}] in {CONFIG_FILE}")
    fw = _table(data, "framework", {"version"})
    if "version" not in fw:
        raise ConfigError("framework.version is required")
    pr = _table(data, "pr", {"max_lines", "exclude", "large_label"})
    review = _table(data, "review", {"providers", "blocking_severities", "lenses_dir"})
    labels = _table(data, "labels", {"prefixes"})
    agents = _table(data, "agents", {"tools", "skills_targets"})
    skills = _table(data, "skills", {"include", "project"})
    council = _table(data, "council", {"roles_dir", "max_reviewers", "require_status"})
    d_pr, d_rev, d_lab, d_ag, d_sk, d_co = PR(), Review(), Labels(), Agents(), Skills(), Council()
    return Config(
        framework=Framework(version=_str(fw, "framework.version", "version", "")),
        pr=PR(
            max_lines=_pos_int(pr.get("max_lines", d_pr.max_lines), "pr.max_lines"),
            exclude=_strs(pr, "pr.exclude", "exclude", d_pr.exclude),
            large_label=_str(pr, "pr.large_label", "large_label", d_pr.large_label),
        ),
        tiers=_tiers(_table(data, "tiers", None)),
        review=Review(
            providers=_tier_map(review.get("providers", {}), "review.providers", _strs_value),
            blocking_severities=_severities(review, d_rev.blocking_severities),
            lenses_dir=_rel_path(
                _str(review, "review.lenses_dir", "lenses_dir", d_rev.lenses_dir),
                "review.lenses_dir",
            ),
        ),
        labels=Labels(prefixes=_prefixes(labels, d_lab.prefixes)),
        agents=Agents(tools=_tools(agents, d_ag.tools), skills_targets=_targets(agents, d_ag)),
        skills=Skills(
            include=_strs(skills, "skills.include", "include", d_sk.include),
            project=_rel_path(
                _str(skills, "skills.project", "project", d_sk.project), "skills.project"
            ),
        ),
        council=Council(
            roles_dir=_rel_path(
                _str(council, "council.roles_dir", "roles_dir", d_co.roles_dir), "council.roles_dir"
            ),
            max_reviewers=_tier_map(
                council.get("max_reviewers", d_co.max_reviewers),
                "council.max_reviewers",
                _non_negative_int,
            ),
            require_status=_require_status(council, d_co.require_status),
        ),
    )


def _table(data: Mapping[str, Any], name: str, keys: set[str] | None) -> dict[str, Any]:
    """Return section ``name`` (empty if absent), rejecting keys outside ``keys``."""
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] must be a table")
    if keys is not None:
        for key in value:
            if key not in keys:
                raise ConfigError(f"unknown key {name}.{key} in {CONFIG_FILE}")
    return value


def _str(table: Mapping[str, Any], where: str, key: str, default: str) -> str:
    """Return the non-empty string at ``key``, or ``default`` when absent."""
    value = table.get(key, default)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{where} must be a non-empty string")
    return value


def _pos_int(value: object, where: str) -> int:
    """Return ``value`` if it is an integer greater than zero (booleans are not)."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{where} must be a positive integer")
    return value


def _non_negative_int(value: object, where: str) -> int:
    """Return ``value`` if it is an integer of zero or more (booleans are not)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigError(f"{where} must be an integer of zero or more")
    return value


def _strs_value(value: object, where: str) -> tuple[str, ...]:
    """Return ``value`` as a tuple if it is a list of non-empty strings."""
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ConfigError(f"{where} must be a list of non-empty strings")
    return tuple(value)


def _strs(
    table: Mapping[str, Any], where: str, key: str, default: tuple[str, ...]
) -> tuple[str, ...]:
    """Return the list of strings at ``key``, or ``default`` when absent."""
    if key not in table:
        return default
    return _strs_value(table[key], where)


def _tier_key(key: str, where: str) -> int:
    """Convert a TOML tier key (``"1"``) to a tier number, rejecting anything else."""
    if key not in {str(t) for t in TIERS}:
        raise ConfigError(f"{where}: tier keys are 1, 2 or 3, not {key!r}")
    return int(key)


def _tier_map[T](value: object, where: str, convert: Callable[[object, str], T]) -> dict[int, T]:
    """Validate a ``{tier: value}`` table, converting each value with ``convert``."""
    if not isinstance(value, dict):
        raise ConfigError(f"{where} must be a table of tier = value")
    return {(_tier_key(str(k), where)): convert(v, f"{where}.{k}") for k, v in value.items()}


def _tiers(table: dict[str, Any]) -> dict[int, tuple[str, ...]]:
    """Validate ``[tiers]``: globs for tiers 1 and 3; tier 2 is everything else."""
    tiers: dict[int, tuple[str, ...]] = _tier_map(table, "tiers", _strs_value)
    if 2 in tiers:
        raise ConfigError("tier 2 is the default for unmatched files; list only tiers 1 and 3")
    return tiers


def _severities(table: Mapping[str, Any], default: tuple[str, ...]) -> tuple[str, ...]:
    """Validate ``review.blocking_severities`` against P0 to P3."""
    values = _strs(table, "review.blocking_severities", "blocking_severities", default)
    for value in values:
        if value not in SEVERITIES:
            raise ConfigError(f"review.blocking_severities: {value!r} is not one of {SEVERITIES}")
    return values


def _prefixes(table: Mapping[str, Any], default: tuple[str, ...]) -> tuple[str, ...]:
    """Validate ``labels.prefixes``: lower-case words, written without the colon."""
    values = _strs(table, "labels.prefixes", "prefixes", default)
    for value in values:
        if not _LABEL.match(value):
            raise ConfigError(f"labels.prefixes: {value!r} must be a lower-case word, no colon")
    return values


def _tools(table: Mapping[str, Any], default: tuple[str, ...]) -> tuple[str, ...]:
    """Validate ``agents.tools`` against the supported tools, each named once."""
    values = _strs(table, "agents.tools", "tools", default)
    for value in values:
        if value not in TOOLS:
            raise ConfigError(f"agents.tools: unknown tool {value!r}; known: {sorted(TOOLS)}")
    if len(set(values)) != len(values):
        raise ConfigError("agents.tools: a tool is listed twice")
    return values


def _rel_path(value: str, where: str) -> str:
    """Require ``value`` to be a relative POSIX path that stays inside the project."""
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ConfigError(f"{where}: {value!r} must be a relative path inside the project")
    return path.as_posix()


def _targets(table: Mapping[str, Any], default: Agents) -> tuple[str, ...]:
    """Validate ``agents.skills_targets``: at least one folder, each inside the project."""
    values = _strs(table, "agents.skills_targets", "skills_targets", default.skills_targets)
    if not values:
        raise ConfigError("agents.skills_targets must name at least one folder")
    return tuple(_rel_path(v, "agents.skills_targets") for v in values)


def _require_status(table: Mapping[str, Any], default: tuple[int, ...]) -> tuple[int, ...]:
    """Validate ``council.require_status``: a list of tier numbers."""
    value = table.get("require_status", list(default))
    if not isinstance(value, list) or not all(
        isinstance(v, int) and not isinstance(v, bool) and v in TIERS for v in value
    ):
        raise ConfigError("council.require_status must be a list of tiers (1, 2 or 3)")
    return tuple(value)

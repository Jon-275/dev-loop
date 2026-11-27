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
SEVERITIES = ("P1", "P2", "P3")
#: Severities ``review.blocking_severities`` accepts: P0, from configs written for 0.2,
#: is accepted and matches no finding.
ACCEPTED_SEVERITIES = ("P0", *SEVERITIES)
#: The kinds of model API a reviewer can use.
REVIEWER_KINDS = ("gemini", "openai", "anthropic")
#: Reviewers devloop knows by name, with their kind; any other name must set ``kind``.
BUILT_IN_REVIEWERS = {
    "gemini": "gemini",
    "reflection": "openai",
    "anthropic": "anthropic",
}
#: The file types a reviewer reads by default: code, and the configuration that ships it.
REVIEWED_FILES = (
    "*.py",
    "*.pyi",
    "*.toml",
    "*.yml",
    "*.yaml",
    "*.sh",
    "*.sql",
    "**/Dockerfile",
    "Dockerfile",
    "**/Dockerfile.*",
    "Dockerfile.*",
)
_NAME = re.compile(r"^[a-z][a-z0-9-]*$")
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
TIERS = (1, 2, 3)
_LABEL = re.compile(r"^[a-z][a-z0-9-]*$")


class ConfigError(ValueError):
    """Raised when ``devloop.toml`` is missing, malformed or holds an invalid value."""


@dataclass(frozen=True)
class Framework:
    """``[framework]``: the framework version the project's templates are pinned to."""

    version: str


@dataclass(frozen=True)
class ExcludeBlock:
    """Generated lines inside a hand-written file: from the ``start`` line to the ``end`` line."""

    path: str
    start: str
    end: str


@dataclass(frozen=True)
class PR:
    """``[pr]``: size limit, what is left out of the count, and the label that waives it."""

    max_lines: int = 800
    exclude: tuple[str, ...] = ()
    exclude_blocks: tuple[ExcludeBlock, ...] = ()
    large_label: str = "large-pr-approved"


@dataclass(frozen=True)
class Reviewer:
    """``[review.reviewers.NAME]``: one model reviewer's API, model, key and dimensions.

    Every field may be left empty: a built-in reviewer fills it in. ``base_url`` comes from
    the trusted checkout's config, never the pull request's, so a key goes only there.
    """

    kind: str = ""
    model: str = ""
    base_url: str = ""
    key: str = ""
    dimensions: dict[str, str] = field(default_factory=dict)
    #: The prompt's instruction for the reply's ``summary`` field ("" for the default).
    summary: str = ""


@dataclass(frozen=True)
class Review:
    """``[review]``: who reviews each tier, what blocks a merge, and what is reviewed.

    ``high_effort`` globs pick the pull requests a model reviews at high effort; ``feedback``
    and ``lessons`` are files the review prompt learns from (empty: none).
    """

    providers: dict[int, tuple[str, ...]] = field(default_factory=dict)
    blocking_severities: tuple[str, ...] = ("P1", "P2")
    lenses_dir: str = "devloop/lenses"
    files: tuple[str, ...] = REVIEWED_FILES
    high_effort: tuple[str, ...] = ("*",)
    feedback: str = ""
    lessons: str = ""
    reviewers: dict[str, Reviewer] = field(default_factory=dict)
    #: Refuse to send a diff or a file that holds a likely secret.
    secret_scan: bool = True
    #: Globs of files the secret scan skips.
    secret_allow: tuple[str, ...] = ()
    #: The most characters an approved outside pull request may send; 0 is no limit.
    outside_max_chars: int = 150_000


@dataclass(frozen=True)
class Labels:
    """``[labels]``: the label words a reply starts with, and the reviews to wait for.

    ``devloop labels`` stays pending until every commit status in ``wait_for`` and every
    check run in ``wait_for_checks`` has finished on the head.
    """

    prefixes: tuple[str, ...] = ("accepted", "rejected")
    wait_for: tuple[str, ...] = ()
    wait_for_checks: tuple[str, ...] = ()


@dataclass(frozen=True)
class Lessons:
    """``[lessons]``: the bug-class seed, the generated lessons page and where rules go.

    ``devloop lessons`` counts accepted findings (``review.feedback``) into the seeded
    classes, writes ``doc``, the top rules between the ``lessons:start`` and
    ``lessons:end`` markers of ``agents``, and the reviewers' patterns (``review.lessons``).
    ``repo`` (``owner/name``) makes PR numbers links; empty leaves them plain.
    """

    seed: str = "devloop/lessons_seed.json"
    doc: str = "docs/lessons-learned.md"
    agents: str = "AGENTS.md"
    repo: str = ""


@dataclass(frozen=True)
class Agents:
    """``[agents]``: the coding agents in use and where rendered skills go."""

    tools: tuple[str, ...] = ("claude", "codex", "gemini", "antigravity", "cursor", "copilot")
    skills_targets: tuple[str, ...] = (".claude/skills", ".agents/skills", ".github/skills")
    commit_attribution: bool = False


#: The framework skills a new project renders: every skill devloop ships.
DEFAULT_SKILLS: tuple[str, ...] = (
    "address-review",
    "council-build",
    "council-design",
    "council-review",
    "decision-record",
    "devloop-setup",
    "lessons-intake",
    "pr-workflow",
    "python-package",
    "release",
    "review-locally",
    "split-stack",
    "stateful-change",
    "testing-and-ci",
    "validate-model-change",
)


@dataclass(frozen=True)
class Skills:
    """``[skills]``: framework skills to render, and the project's own skills folder."""

    include: tuple[str, ...] = DEFAULT_SKILLS
    project: str = "devloop/skills"


@dataclass(frozen=True)
class Council:
    """``[council]``: where role cards live, reviewer budget per tier, required status."""

    roles_dir: str = "devloop/council"
    max_reviewers: dict[int, int] = field(default_factory=lambda: {1: 5, 2: 2})
    require_status: tuple[int, ...] = (1,)
    #: Library packs the owner chose not to add; their signals are not warned about.
    declined: tuple[str, ...] = ()


DEFAULT_SECTIONS = {
    "breaking": "Breaking changes",
    "added": "Added",
    "changed": "Changed",
    "fixed": "Fixed",
    "security": "Security",
}


@dataclass(frozen=True)
class Changelog:
    """``[changelog]``: fragment folder, changelog file, sections, and paths needing an entry."""

    dir: str = "changes"
    file: str = "CHANGELOG.md"
    sections: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_SECTIONS))
    code: tuple[str, ...] = ("src/*", ".github/*", "pyproject.toml")


@dataclass(frozen=True)
class Docs:
    """``[docs]``: which files are documentation, and which tests read documents.

    A pull request that changes only documentation skips the code checks but still runs
    the test files matching ``tests`` that mention a ``.md`` file.
    """

    files: tuple[str, ...] = (
        "*.md",
        "docs/*.svg",
        "docs/*.png",
        "docs/*.jpg",
        "docs/*.jpeg",
        "docs/*.gif",
        "docs/*.webp",
    )
    tests: tuple[str, ...] = ("tests/*test_*.py",)


#: What a file under ``docs/`` may end with to be a document for the merge gate.
GATE_DOCUMENTS = ("md", "svg", "png", "jpg", "jpeg", "gif", "webp")
#: Front pages at the root that are documents for the merge gate.
GATE_FRONT_PAGES = ("README.md", "CONTRIBUTING.md", "SUPPORT.md", "CODE_OF_CONDUCT.md")


def gate_light(changelog_dir: str = "changes") -> tuple[str, ...]:
    """Return the gate's default documents: docs, changelog fragments and the front pages."""
    docs = tuple(f"docs/**/*.{kind}" for kind in GATE_DOCUMENTS)
    return (*docs, f"{changelog_dir}/*.md", *GATE_FRONT_PAGES)


@dataclass(frozen=True)
class GateCheck:
    """One check run the merge gate needs: its name, and the id of the app that makes it."""

    name: str
    app: int


@dataclass(frozen=True)
class Gate:
    """``[gate]``: the one merge status, its two lanes and the reviews the strict lane needs.

    In ``light`` and ``control`` globs ``*`` stays inside one folder and ``**`` crosses
    folders. ``control`` adds to the built-in control files; it cannot remove one.
    """

    context: str = "Merge gate"
    light: tuple[str, ...] = gate_light()
    control: tuple[str, ...] = ()
    statuses: tuple[str, ...] = ("Review labels",)
    checks: tuple[GateCheck, ...] = ()
    light_blocks: tuple[str, ...] = ("Review labels",)
    trusted_bots: tuple[str, ...] = ("dependabot[bot]",)
    max_light_files: int = 100


@dataclass(frozen=True)
class Config:
    """A validated ``devloop.toml``."""

    framework: Framework
    pr: PR = field(default_factory=PR)
    tiers: dict[int, tuple[str, ...]] = field(default_factory=dict)
    review: Review = field(default_factory=Review)
    labels: Labels = field(default_factory=Labels)
    lessons: Lessons = field(default_factory=Lessons)
    agents: Agents = field(default_factory=Agents)
    skills: Skills = field(default_factory=Skills)
    council: Council = field(default_factory=Council)
    changelog: Changelog = field(default_factory=Changelog)
    docs: Docs = field(default_factory=Docs)
    gate: Gate = field(default_factory=Gate)

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
    sections = {
        "framework",
        "pr",
        "tiers",
        "review",
        "labels",
        "lessons",
        "agents",
        "skills",
        "council",
        "changelog",
        "docs",
        "gate",
    }
    for name in data:
        if name not in sections:
            raise ConfigError(f"unknown section [{name}] in {CONFIG_FILE}")
    fw = _table(data, "framework", {"version"})
    if "version" not in fw:
        raise ConfigError("framework.version is required")
    pr = _table(data, "pr", {"max_lines", "exclude", "exclude_blocks", "large_label"})
    review = _table(
        data,
        "review",
        {
            "providers",
            "blocking_severities",
            "lenses_dir",
            "files",
            "high_effort",
            "feedback",
            "lessons",
            "reviewers",
            "secret_scan",
            "secret_allow",
            "outside_max_chars",
        },
    )
    labels = _table(data, "labels", {"prefixes", "wait_for", "wait_for_checks"})
    lessons = _table(data, "lessons", {"seed", "doc", "agents", "repo"})
    agents = _table(data, "agents", {"tools", "skills_targets", "commit_attribution"})
    skills = _table(data, "skills", {"include", "project"})
    council = _table(data, "council", {"roles_dir", "max_reviewers", "require_status", "declined"})
    log = _table(data, "changelog", {"dir", "file", "sections", "code"})
    docs = _table(data, "docs", {"files", "tests"})
    d_pr, d_rev, d_lab, d_ag, d_sk, d_co = PR(), Review(), Labels(), Agents(), Skills(), Council()
    changelog = _changelog(log)
    reviewers = _reviewers(_table(review, "reviewers", None))
    wait_for = _strs(labels, "labels.wait_for", "wait_for", d_lab.wait_for)
    return Config(
        framework=Framework(version=_str(fw, "framework.version", "version", "")),
        pr=PR(
            max_lines=_pos_int(pr.get("max_lines", d_pr.max_lines), "pr.max_lines"),
            exclude=_strs(pr, "pr.exclude", "exclude", d_pr.exclude),
            exclude_blocks=_blocks(pr.get("exclude_blocks", [])),
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
            files=_non_empty(review, "review.files", "files", d_rev.files),
            high_effort=_strs(review, "review.high_effort", "high_effort", d_rev.high_effort),
            feedback=_optional_path(review, "review.feedback", "feedback"),
            lessons=_optional_path(review, "review.lessons", "lessons"),
            reviewers=reviewers,
            secret_scan=_bool(review.get("secret_scan", d_rev.secret_scan), "review.secret_scan"),
            secret_allow=_strs(review, "review.secret_allow", "secret_allow", d_rev.secret_allow),
            outside_max_chars=_non_negative_int(
                review.get("outside_max_chars", d_rev.outside_max_chars),
                "review.outside_max_chars",
            ),
        ),
        labels=Labels(
            prefixes=_prefixes(labels, d_lab.prefixes),
            wait_for=wait_for,
            wait_for_checks=_strs(
                labels, "labels.wait_for_checks", "wait_for_checks", d_lab.wait_for_checks
            ),
        ),
        lessons=_lessons(lessons),
        agents=Agents(
            tools=_tools(agents, d_ag.tools),
            skills_targets=_targets(agents, d_ag),
            commit_attribution=_bool(
                agents.get("commit_attribution", d_ag.commit_attribution),
                "agents.commit_attribution",
            ),
        ),
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
            declined=_strs(council, "council.declined", "declined", d_co.declined),
        ),
        changelog=changelog,
        docs=Docs(
            files=_strs(docs, "docs.files", "files", Docs().files),
            tests=_strs(docs, "docs.tests", "tests", Docs().tests),
        ),
        gate=_gate(_table(data, "gate", _GATE_KEYS), changelog.dir, wait_for, reviewers),
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


def _bool(value: object, where: str) -> bool:
    """Return ``value`` if it is true or false."""
    if not isinstance(value, bool):
        raise ConfigError(f"{where} must be true or false")
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


def _non_empty(
    table: Mapping[str, Any], where: str, key: str, default: tuple[str, ...]
) -> tuple[str, ...]:
    """Return the list of strings at ``key`` (``default`` when absent); it may not be empty."""
    values = _strs(table, where, key, default)
    if not values:
        raise ConfigError(f"{where} must name at least one pattern")
    return values


def _optional_path(table: Mapping[str, Any], where: str, key: str) -> str:
    """Return the relative path at ``key``, or ``""`` when absent."""
    return _rel_path(_str(table, where, key, "-"), where) if key in table else ""


def _reviewers(table: Mapping[str, Any]) -> dict[str, Reviewer]:
    """Validate ``[review.reviewers.NAME]`` tables; an unknown name must set ``kind``."""
    reviewers: dict[str, Reviewer] = {}
    for name, entry in table.items():
        where = f"review.reviewers.{name}"
        if not _NAME.match(name):
            raise ConfigError(f"{where}: a reviewer name is a lower-case word")
        if not isinstance(entry, dict):
            raise ConfigError(f"[{where}] must be a table")
        for key in entry:
            if key not in {"kind", "model", "base_url", "key", "dimensions", "summary"}:
                raise ConfigError(f"unknown key {where}.{key} in {CONFIG_FILE}")
        kind = _str(entry, f"{where}.kind", "kind", BUILT_IN_REVIEWERS.get(name, "-"))
        if kind not in REVIEWER_KINDS:
            raise ConfigError(f"{where}.kind must be one of {REVIEWER_KINDS}")
        model = _str(entry, f"{where}.model", "model", "-") if "model" in entry else ""
        url = _str(entry, f"{where}.base_url", "base_url", "-") if "base_url" in entry else ""
        if url and not url.startswith("https://"):
            raise ConfigError(f"{where}.base_url must be an https:// URL")
        key = _str(entry, f"{where}.key", "key", "-") if "key" in entry else ""
        if key and not _ENV_NAME.match(key):
            raise ConfigError(f"{where}.key must name an environment variable (A_KEY)")
        dims = entry.get("dimensions", {})
        if not isinstance(dims, dict) or not all(
            isinstance(v, str) and v and re.fullmatch(r"[A-Za-z0-9]+", k) for k, v in dims.items()
        ):
            raise ConfigError(f"{where}.dimensions must be a table of TAG = name")
        summary = _str(entry, f"{where}.summary", "summary", "-") if "summary" in entry else ""
        reviewers[name] = Reviewer(kind, model, url, key, dict(dims), summary)
    return reviewers


def _severities(table: Mapping[str, Any], default: tuple[str, ...]) -> tuple[str, ...]:
    """Validate ``review.blocking_severities`` against P1 to P3."""
    values = _strs(table, "review.blocking_severities", "blocking_severities", default)
    for value in values:
        if value not in ACCEPTED_SEVERITIES:
            raise ConfigError(
                f"review.blocking_severities: {value!r} is not one of {ACCEPTED_SEVERITIES}"
            )
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


def _blocks(value: object) -> tuple[ExcludeBlock, ...]:
    """Validate ``pr.exclude_blocks``: a list of ``{path, start, end}`` tables."""
    where = "pr.exclude_blocks"
    if not isinstance(value, list):
        raise ConfigError(f"{where} must be a list of {{ path, start, end }} tables")
    blocks = []
    for i, item in enumerate(value):
        if not isinstance(item, dict) or set(item) != {"path", "start", "end"}:
            raise ConfigError(f"{where}[{i}] must have exactly path, start and end")
        path, start, end = (
            _str(item, f"{where}[{i}].{k}", k, "") for k in ("path", "start", "end")
        )
        blocks.append(ExcludeBlock(_rel_path(path, f"{where}[{i}].path"), start, end))
    return tuple(blocks)


def _changelog(table: Mapping[str, Any]) -> Changelog:
    """Validate ``[changelog]``; a section key is the ``<section>`` in a fragment's name."""
    d = Changelog()
    sections = table.get("sections", d.sections)
    if not isinstance(sections, dict) or not sections:
        raise ConfigError("changelog.sections must be a table of name = heading")
    for key, heading in sections.items():
        if not re.fullmatch(r"[a-z]+", key) or not isinstance(heading, str) or not heading:
            raise ConfigError(f"changelog.sections.{key}: a lower-case name and a heading")
    return Changelog(
        dir=_rel_path(_str(table, "changelog.dir", "dir", d.dir), "changelog.dir"),
        file=_rel_path(_str(table, "changelog.file", "file", d.file), "changelog.file"),
        sections=dict(sections),
        code=_strs(table, "changelog.code", "code", d.code),
    )


def _lessons(table: Mapping[str, Any]) -> Lessons:
    """Validate ``[lessons]``: three paths inside the project and an optional ``owner/name``."""
    d = Lessons()
    repo = table.get("repo", "")
    if not isinstance(repo, str) or (repo and not re.fullmatch(r"[\w.-]+/[\w.-]+", repo)):
        raise ConfigError("lessons.repo must be owner/name")
    return Lessons(
        seed=_rel_path(_str(table, "lessons.seed", "seed", d.seed), "lessons.seed"),
        doc=_rel_path(_str(table, "lessons.doc", "doc", d.doc), "lessons.doc"),
        agents=_rel_path(_str(table, "lessons.agents", "agents", d.agents), "lessons.agents"),
        repo=repo,
    )


_GATE_KEYS = {
    "context",
    "light",
    "control",
    "statuses",
    "checks",
    "light_blocks",
    "trusted_bots",
    "max_light_files",
}


def _globs(table: Mapping[str, Any], key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """Return the gate's path globs at ``key``: each a relative path inside the project."""
    values = _strs(table, f"gate.{key}", key, default)
    for value in values:
        if value.startswith("/") or "\\" in value or ".." in value.split("/"):
            raise ConfigError(f"gate.{key}: {value!r} must be a relative path inside the project")
    return values


def _gate_checks(value: object) -> tuple[GateCheck, ...]:
    """Validate ``gate.checks``: a list of ``{ name, app }`` tables, ``app`` an app's id."""
    where = "gate.checks"
    if not isinstance(value, list):
        raise ConfigError(f"{where} must be a list of {{ name, app }} tables")
    checks = []
    for i, item in enumerate(value):
        if not isinstance(item, dict) or set(item) != {"name", "app"}:
            raise ConfigError(f"{where}[{i}] must have exactly name and app")
        name = _str(item, f"{where}[{i}].name", "name", "")
        checks.append(GateCheck(name, _pos_int(item["app"], f"{where}[{i}].app")))
    return tuple(checks)


#: Statuses other devloop jobs post, whatever the configuration.
OTHER_STATUSES = ("Review labels", "Council review")


def _gate(
    table: Mapping[str, Any],
    changelog_dir: str,
    wait_for: tuple[str, ...],
    reviewers: Mapping[str, Reviewer],
) -> Gate:
    """Validate ``[gate]``.

    ``light`` defaults to the documents with the configured changelog folder, and
    ``statuses`` to the reviews ``labels.wait_for`` names plus ``Review labels``. The
    strict lane must need something, every signal has its own name, and the gate never
    waits for its own status. Its status has a name no other devloop job posts (the
    labels check, the council, a reviewer or a ``labels.wait_for`` status): that job
    would write its own result under the gate's name.
    """
    d = Gate()
    context = _str(table, "gate.context", "context", d.context)
    derived = tuple(dict.fromkeys((*wait_for, *d.statuses)))
    statuses = _strs(table, "gate.statuses", "statuses", derived)
    checks = _gate_checks(table.get("checks", []))
    light_blocks = _strs(table, "gate.light_blocks", "light_blocks", d.light_blocks)
    needed = [*statuses, *(check.name for check in checks)]
    if not needed:
        raise ConfigError("gate.statuses and gate.checks are both empty: the strict lane needs one")
    if len(set(needed)) != len(needed):
        raise ConfigError("gate.statuses and gate.checks name a status or a check twice")
    if context in (*needed, *light_blocks):
        raise ConfigError(f"gate.context {context!r}: the gate cannot wait for its own status")
    reviews = [f"{name.capitalize()} review" for name in (*BUILT_IN_REVIEWERS, *reviewers)]
    if context.casefold() in {name.casefold() for name in (*OTHER_STATUSES, *wait_for, *reviews)}:
        raise ConfigError(f"gate.context {context!r} is a status another devloop job posts")
    return Gate(
        context=context,
        light=_globs(table, "light", gate_light(changelog_dir)),
        control=_globs(table, "control", d.control),
        statuses=statuses,
        checks=checks,
        light_blocks=light_blocks,
        trusted_bots=_strs(table, "gate.trusted_bots", "trusted_bots", d.trusted_bots),
        max_light_files=_pos_int(
            table.get("max_light_files", d.max_light_files), "gate.max_light_files"
        ),
    )

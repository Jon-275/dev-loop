"""The council: role cards, who a change summons, and checks that the council is current.

A role card is one Markdown file per role in ``council.roles_dir`` (default
``devloop/council/``), read by every tool's ``council-review`` skill. Its frontmatter
says when the role is summoned and which reviewer runs it; its body holds the role's
fields, one ``Field: text`` line each (indented lines continue a field)::

    ---
    name: payments                       # the file's name, payments.md
    title: Payments Engineer
    summoned_by: ["src/billing/**"]      # globs, matched as in [tiers]
    tiers: [1, 2]
    reviewer: money                      # roles sharing a reviewer run in one pass
    ---
    Mandate: money is never lost, doubled or rounded wrong.
    Checks: ...
    Severity anchors: ...
    Proof methods: ...
    Decisions enforced: docs/decisions/0001-money.md   (optional, comma-separated)
    Out of scope: ...                                   (optional)
    Known false positives: ...                          (optional)

``devloop council check`` (and ``devloop doctor``) report a bad card, a glob that matches
no file, a decision file that does not exist, and a council with more reviewers for a
tier than ``council.max_reviewers`` allows. ``devloop council plan`` prints which roles
and reviewers a change summons.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

from devloop.config import CONFIG_FILE, Config, ConfigError, load_config
from devloop.git import git

KEYS = ("name", "title", "summoned_by", "tiers", "reviewer")
REQUIRED = ("Mandate", "Checks", "Severity anchors", "Proof methods")
FIELDS = (*REQUIRED, "Decisions enforced", "Out of scope", "Known false positives")
_SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_LINE = re.compile(r"^([A-Z][A-Za-z ]*):(?: (.*))?$")
_SKIP_DIRS = frozenset({".git", ".venv", "node_modules", "__pycache__"})


class CouncilError(ValueError):
    """Raised when a role card cannot be read or breaks the card format."""


@dataclass(frozen=True)
class RoleCard:
    """One council role: when it is summoned, who runs it, and what it checks."""

    name: str
    title: str
    summoned_by: tuple[str, ...]
    tiers: tuple[int, ...]
    reviewer: str
    fields: dict[str, str]

    @property
    def decisions(self) -> tuple[str, ...]:
        """The decision files this role enforces."""
        text = self.fields.get("Decisions enforced", "")
        return tuple(part.strip() for part in text.split(",") if part.strip())


@dataclass(frozen=True)
class Plan:
    """The roles a change summons, and those roles grouped by the reviewer that runs them."""

    tier: int
    roles: tuple[RoleCard, ...]
    reviewers: dict[str, tuple[str, ...]]


def _frontmatter(lines: list[str]) -> tuple[dict[str, object], list[str]]:
    """Split ``lines`` into the parsed frontmatter and the body lines."""
    if not lines or lines[0].strip() != "---":
        raise CouncilError("a role card must start with --- frontmatter")
    try:
        end = next(i for i, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration:
        raise CouncilError("the frontmatter is never closed with ---") from None
    meta: dict[str, object] = {}
    for line in lines[1:end]:
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if key not in KEYS:
            raise CouncilError(f"{key} is not a role card key ({', '.join(KEYS)})")
        try:
            meta[key] = json.loads(value) if value[:1] in '["' and value else value
        except ValueError:
            raise CouncilError(f"{key}: {value} is not valid") from None
    return meta, lines[end + 1 :]


def _body(lines: list[str]) -> dict[str, str]:
    """Parse the body's ``Field: text`` lines, joining indented continuation lines."""
    fields: dict[str, str] = {}
    current = ""
    for number, line in enumerate(lines, 1):
        if not line.strip():
            current = ""
            continue
        if line[:1] in " \t":
            if not current:
                raise CouncilError(f"body line {number}: an indented line must continue a field")
            fields[current] = f"{fields[current]} {line.strip()}".strip()
            continue
        match = _LINE.match(line)
        if not match:
            raise CouncilError(f"body line {number} is not 'Field: text'")
        name = match.group(1)
        if name not in FIELDS:
            raise CouncilError(f"{name} is not a role card field ({', '.join(FIELDS)})")
        if name in fields:
            raise CouncilError(f"{name} appears twice")
        fields[name], current = (match.group(2) or "").strip(), name
    for name in REQUIRED:
        if name not in fields:
            raise CouncilError(f"{name} is missing")
        if not fields[name]:
            raise CouncilError(f"{name} is empty")
    return fields


def parse_card(text: str, stem: str) -> RoleCard:
    """Parse and validate the role card in ``text``, read from ``<stem>.md``."""
    meta, body = _frontmatter(text.splitlines())
    name, title, reviewer = meta.get("name"), meta.get("title"), meta.get("reviewer")
    summoned, tiers = meta.get("summoned_by"), meta.get("tiers")
    if name != stem:
        raise CouncilError(f"name {name!r} must match its file {stem}.md")
    if not isinstance(title, str) or not title:
        raise CouncilError("title is required")
    if (
        not isinstance(summoned, list)
        or not summoned
        or not all(isinstance(g, str) and g for g in summoned)
    ):
        raise CouncilError('summoned_by must be a non-empty list of globs, like ["src/**"]')
    if (
        not isinstance(tiers, list)
        or not tiers
        or not all(type(t) is int and t in (1, 2, 3) for t in tiers)
    ):
        raise CouncilError("tiers must be a non-empty list of 1, 2 or 3")
    if not isinstance(reviewer, str) or not _SLUG.match(reviewer):
        raise CouncilError("reviewer must be lower-case letters, digits and single hyphens")
    return RoleCard(
        name=stem,
        title=title,
        summoned_by=tuple(summoned),
        tiers=tuple(sorted(set(tiers))),
        reviewer=reviewer,
        fields=_body(body),
    )


def load_cards(folder: Path, label: str = "") -> dict[str, RoleCard]:
    """Load every role card in ``folder`` (none if it does not exist), keyed by name.

    Raises:
        CouncilError: a card cannot be read or is invalid; the message names its file.

    """
    if not folder.is_dir():
        return {}
    prefix = f"{label}/" if label else ""
    cards: dict[str, RoleCard] = {}
    for path in sorted(folder.glob("*.md")):
        if path.name == "README.md":
            continue
        try:
            cards[path.stem] = parse_card(path.read_text(encoding="utf-8"), path.stem)
        except (OSError, UnicodeDecodeError) as error:
            raise CouncilError(f"{prefix}{path.name}: cannot be read ({error})") from None
        except CouncilError as error:
            raise CouncilError(f"{prefix}{path.name}: {error}") from None
    return cards


def plan(cards: Mapping[str, RoleCard], paths: Sequence[str], tier: int) -> Plan:
    """Return the roles a tier-``tier`` change to ``paths`` summons, grouped by reviewer."""
    roles = tuple(
        card
        for _, card in sorted(cards.items())
        if tier in card.tiers and any(fnmatchcase(p, g) for p in paths for g in card.summoned_by)
    )
    reviewers: dict[str, tuple[str, ...]] = {}
    for card in roles:
        reviewers[card.reviewer] = (*reviewers.get(card.reviewer, ()), card.name)
    return Plan(tier=tier, roles=roles, reviewers=dict(sorted(reviewers.items())))


def project_files(root: Path) -> list[str]:
    """Return the project's files: git's tracked and unignored ones, or a walk without git."""
    try:
        out = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
        return [p for p in out.split("\0") if p]
    except (OSError, subprocess.CalledProcessError):
        return [
            p.relative_to(root).as_posix()
            for p in root.rglob("*")
            if p.is_file() and not _SKIP_DIRS & set(p.relative_to(root).parts)
        ]


def problems(root: Path, config: Config | None = None) -> list[str]:
    """Return every problem with the project's council; empty when it is current."""
    config = config or load_config(root / CONFIG_FILE)
    label = config.council.roles_dir
    try:
        cards = load_cards(root / label, label)
    except CouncilError as error:
        return [str(error)]
    if not cards:
        return []
    files = project_files(root)
    found: list[str] = []
    for name, card in cards.items():
        where = f"{label}/{name}.md"
        for decision in card.decisions:
            if not (root / decision).is_file():
                found.append(f"{where}: Decisions enforced names {decision}, which does not exist")
        dead = [g for g in card.summoned_by if not any(fnmatchcase(f, g) for f in files)]
        if dead:
            found.append(f"{where}: summoned_by matches no file: {', '.join(dead)}")
    for tier, budget in sorted(config.council.max_reviewers.items()):
        names = sorted({c.reviewer for c in cards.values() if tier in c.tiers})
        if len(names) > budget:
            found.append(
                f"council: {len(names)} reviewers ({', '.join(names)}) can review a "
                f"tier-{tier} pull request but council.max_reviewers allows {budget}; "
                "group roles under fewer reviewers"
            )
    return found


def _changed(root: Path, base: str) -> list[str]:
    """Return the files changed on this branch since it left ``base``."""
    return [p for p in git(root, "diff", "--name-only", "-z", f"{base}...HEAD").split("\0") if p]


def main(argv: Sequence[str]) -> int:
    """Run ``devloop council``: ``check`` the cards, or ``plan`` who reviews a change."""
    from devloop.review.run import pr_tier

    parser = argparse.ArgumentParser(prog="devloop council", description=__doc__.split("\n")[0])
    actions = parser.add_subparsers(dest="action", required=True)
    for action, text in (("check", "report every council problem"), ("plan", "who reviews")):
        sub = actions.add_parser(action, help=text)
        sub.add_argument("--root", type=Path, default=Path.cwd(), help="project root")
        if action == "plan":
            sub.add_argument("--base", default="origin/main", help="the branch's base")
            sub.add_argument("--paths", nargs="*", help="the changed files (default: git)")
    args = parser.parse_args(list(argv))
    try:
        root: Path = args.root.resolve()
        config = load_config(root / CONFIG_FILE)
        if args.action == "check":
            found = problems(root, config)
            for problem in found:
                print(f"problem: {problem}")
            print(f"{len(found)} problem(s) found" if found else "council is current")
            return 1 if found else 0
        label = config.council.roles_dir
        cards = load_cards(root / label, label)
        paths = args.paths if args.paths is not None else _changed(root, args.base)
    except (
        ConfigError,
        CouncilError,
        OSError,
        UnicodeDecodeError,
        subprocess.CalledProcessError,
    ) as error:
        print(f"devloop council: {error}", file=sys.stderr)
        return 2
    chosen = plan(cards, paths, pr_tier(paths, config.tiers))
    budget = config.council.max_reviewers.get(chosen.tier)
    print(
        f"Tier {chosen.tier}: {len(chosen.reviewers)} reviewer(s), "
        + (f"budget {budget}" if budget is not None else "no budget set")
    )
    titles = {card.name: card.title for card in chosen.roles}
    for reviewer, names in chosen.reviewers.items():
        print(f"- {reviewer}: " + ", ".join(f"{n} ({titles[n]})" for n in names))
    return 0

"""``devloop lessons``: turn accepted review findings into lessons every author reads first.

So agents working on different parts learn from each other and from earlier mistakes.
Ported from gtm_toolkit's ``scripts/review_lessons.py``.

Inputs:

- ``lessons.seed``: the bug classes review found, each with a short rule, a minimal bad
  and good example, keywords and the PRs it was seen in.
- ``review.feedback`` (:mod:`devloop.review.feedback`): findings maintainers accepted. Each
  one whose title matches a class's keywords counts towards it and adds its PR; rejected
  findings never make a lesson.
- Optionally, council notes (``--notes DIR`` or ``REVIEW_NOTES_DIR``): finding headings in
  ``review-pr*.md`` and ``phase2-exit*.md`` count the same way.

Outputs, deterministic (no timestamps), so unchanged inputs change nothing:

- ``lessons.doc``: the classes ranked by how often review found them, with counts;
- ``lessons.agents``: the top 10 rules, between the ``lessons:start`` and ``lessons:end``
  markers, so every agent sees them on its first read;
- ``review.lessons``: the ranked rules every reviewer checks first.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from devloop.config import Lessons

TOP_RULES = 10
#: How many accepted findings that match no class the doc lists (the latest).
UNMATCHED_SHOWN = 10
START, END = "<!-- lessons:start -->", "<!-- lessons:end -->"
#: Finding headings in council notes: ``### P1-1 Title`` or ``- **P2-3 Title.**``.
NOTE_FINDING = re.compile(r"^(?:#{2,4}\s+|-\s+\*\*)(P[0-3](?:-\d+)?)\s+(.+?)(?:\*\*|$)", re.M)
NOTE_FILES = ("review-pr*.md", "phase2-exit*.md")


@dataclass
class LessonClass:
    """One bug class and the evidence of how often review found it."""

    id: str
    title: str
    rule: str
    bad: str
    good: str
    keywords: tuple[str, ...]
    prs: set[int] = field(default_factory=set)
    seen: int = 0
    sources: set[str] = field(default_factory=set)

    def matches(self, text: str) -> bool:
        """Whether ``text`` (a finding title) has one of the class's keywords as whole words.

        ``race`` matches "a race on promote" but not "stack trace"; a keyword
        is a word or a phrase, never a fragment of a longer word.
        """
        lowered = text.lower()
        return any(
            re.search(rf"(?<![\w-]){re.escape(keyword)}(?![\w-])", lowered)
            for keyword in self.keywords
        )


class LessonsError(ValueError):
    """An input exists but cannot be read; nothing is written."""


def load_json(path: Path) -> dict[str, Any]:
    """Return the JSON object at ``path``, or an empty one when the file is missing.

    Raises:
        LessonsError: the file exists but is not a JSON object, so the lessons are never
            rebuilt from an input that silently read as empty.

    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as error:
        raise LessonsError(f"{path} cannot be read: {error}") from None
    if not isinstance(data, dict):
        raise LessonsError(f"{path} is not a JSON object")
    return data


def seed_classes(seed: Mapping[str, Any]) -> list[LessonClass]:
    """Return the seeded classes; each seeded PR counts once."""
    source = str(seed.get("source") or "seed")
    classes = []
    for item in seed.get("classes") or []:
        prs = {int(n) for n in item.get("prs") or []}
        classes.append(
            LessonClass(
                id=item["id"],
                title=item["title"],
                rule=item["rule"],
                bad=item["bad"],
                good=item["good"],
                keywords=tuple(k.lower() for k in item.get("keywords") or []),
                prs=prs,
                seen=len(prs),
                sources={source} if prs else set(),
            )
        )
    return classes


def note_findings(folder: Path | None) -> list[tuple[str, int | None]]:
    """Return finding titles in the council notes in ``folder``, with the PR each file reviews.

    ``None`` or a missing folder gives none.
    """
    if folder is None or not folder.is_dir():
        return []
    found: list[tuple[str, int | None]] = []
    for pattern in NOTE_FILES:
        for path in sorted(folder.glob(pattern)):
            number = re.search(r"pr(\d+)", path.name)
            pr = int(number.group(1)) if number else None
            found += [(m.group(2).strip(), pr) for m in NOTE_FINDING.finditer(path.read_text())]
    return found


def tally(
    classes: Sequence[LessonClass],
    records: Iterable[Mapping[str, Any]],
    notes: Iterable[tuple[str, int | None]] = (),
) -> list[Mapping[str, Any]]:
    """Count accepted findings and note findings into ``classes``; return the unmatched ones.

    A finding counts towards the first class whose keywords match its title.
    """
    unmatched: list[Mapping[str, Any]] = []
    for record in records:
        if record.get("outcome") != "accepted":
            continue
        title = str(record.get("title") or "")
        target = next((c for c in classes if c.matches(title)), None)
        if target is None:
            unmatched.append(record)
            continue
        target.seen += 1
        target.sources.add(str(record.get("reviewer") or "review"))
        if isinstance(record.get("pr"), int):
            target.prs.add(record["pr"])
    for title, pr in notes:
        target = next((c for c in classes if c.matches(title)), None)
        if target is not None:
            target.seen += 1
            target.sources.add("council notes")
            if pr is not None:
                target.prs.add(pr)
    return unmatched


def ranked(classes: Iterable[LessonClass]) -> list[LessonClass]:
    """Classes by how often review found them, most first; ties by id."""
    return sorted(classes, key=lambda c: (-c.seen, c.id))


def _times(count: int) -> str:
    """``count`` as "1 time" or "N times"."""
    return f"{count} time" if count == 1 else f"{count} times"


def _prs(lesson: LessonClass, repo: str) -> str:
    """Return the PRs a class was seen in: links when ``repo`` is set, else plain numbers."""
    if repo:
        url = f"https://github.com/{repo}/pull"
        return ", ".join(f"[#{n}]({url}/{n})" for n in sorted(lesson.prs)) or "none yet"
    return ", ".join(f"#{n}" for n in sorted(lesson.prs)) or "none yet"


def lessons_doc(
    classes: Sequence[LessonClass],
    unmatched: Sequence[Mapping[str, Any]],
    repo: str = "",
    seed: str = "devloop/lessons_seed.json",
    doc: str = "docs/lessons-learned.md",
    agents: str = "AGENTS.md",
) -> str:
    """Return the full ``lessons-learned.md``."""
    lines = [
        "# Lessons learned from review",
        "",
        "Read this before changing code. Each class below is a bug class review found in this",
        "repository, with how many times it was seen; a class seen only once is listed too.",
        "The rule says how to avoid it; the examples are minimal. Classes are ranked by how",
        "often they were seen. Generated by `devloop lessons` from accepted review findings and",
        f"the classes seeded in `{seed}`; do not edit by hand. The top 10 rules are also in",
        f"`{agents}`.",
        "",
    ]
    for number, lesson in enumerate(classes, start=1):
        lines += [
            f"## {number}. {lesson.title}",
            "",
            f"**Rule:** {lesson.rule}",
            "",
            f"Seen {_times(lesson.seen)} ({', '.join(sorted(lesson.sources))}). "
            f"PRs: {_prs(lesson, repo)}.",
            "",
            "Bad:",
            "",
            "```python",
            lesson.bad,
            "```",
            "",
            "Good:",
            "",
            "```python",
            lesson.good,
            "```",
            "",
        ]
    if unmatched:
        lines += [
            "## Accepted findings not yet in a class",
            "",
            f"Add a class to `{seed}` when one of these recurs.",
            "",
        ]
        shown = list(unmatched)[-UNMATCHED_SHOWN:]
        if len(unmatched) > len(shown):
            lines += [
                f"The latest {len(shown)} of {len(unmatched)} are shown; the other "
                f"{len(unmatched) - len(shown)} are in the review feedback store.",
                "",
            ]
        for record in shown:
            title = " ".join(str(record.get("title") or "").replace("<!--", "").split())[:200]
            lines.append(f"- {record.get('reviewer', '?')}, #{record.get('pr', '?')}: {title}")
        lines.append("")
    return "\n".join(lines)


def agents_section(classes: Sequence[LessonClass], doc: str = "docs/lessons-learned.md") -> str:
    """Return the generated top-rules section of ``AGENTS.md``, markers included."""
    lines = [
        START,
        "## Lessons from review (top 10, read before changing code)",
        "",
        "Generated by `devloop lessons`; the full list, with examples and PR links,",
        f"is [{doc}]({doc}).",
        "",
    ]
    for number, lesson in enumerate(classes[:TOP_RULES], start=1):
        lines.append(f"{number}. **{lesson.title}.** {lesson.rule} (seen {_times(lesson.seen)})")
    lines.append(END)
    return "\n".join(lines)


def with_section(agents: str, section: str) -> str:
    """Return ``agents`` with the section replaced, or added before its structure heading.

    Without markers the section goes before ``## Repository Structure``, or at the end.
    """
    if START in agents and END in agents:
        before, rest = agents.split(START, 1)
        after = rest.split(END, 1)[1]
        return before + section + after
    anchor = "## Repository Structure"
    if anchor in agents:
        return agents.replace(anchor, f"{section}\n\n{anchor}", 1)
    return agents.rstrip("\n") + "\n\n" + section + "\n"


def reviewer_patterns(classes: Sequence[LessonClass]) -> dict[str, Any]:
    """Return the ranked rules both reviewers read as known failure patterns."""
    return {
        "version": 1,
        "classes": [
            {"id": c.id, "title": c.title, "rule": c.rule, "seen": c.seen} for c in classes
        ],
    }


def build(
    seed: Mapping[str, Any], store: Mapping[str, Any], notes_dir: Path | None
) -> tuple[list[LessonClass], list[Mapping[str, Any]]]:
    """Return the ranked classes and the unmatched accepted findings."""
    classes = seed_classes(seed)
    records = [r for r in store.get("records") or [] if isinstance(r, Mapping)]
    unmatched = tally(classes, records, note_findings(notes_dir))
    return ranked(classes), unmatched


@dataclass(frozen=True)
class Outputs:
    """The three files the generator writes, as text."""

    doc: str
    agents: str
    patterns: str


def render(
    seed: Mapping[str, Any],
    store: Mapping[str, Any],
    agents: str,
    notes_dir: Path | None = None,
    settings: Lessons | None = None,
) -> Outputs:
    """Return what the generator writes for these inputs: the command and the tests use this."""
    s = Lessons() if settings is None else settings
    classes, unmatched = build(seed, store, notes_dir)
    return Outputs(
        doc=lessons_doc(classes, unmatched, s.repo, s.seed, s.doc, s.agents),
        agents=with_section(agents, agents_section(classes, s.doc)),
        patterns=json.dumps(reviewer_patterns(classes), indent=2) + "\n",
    )


def main(root: Path, notes: Path | None) -> int:
    """Write the lessons page, the rules block and the reviewers' patterns; 2 on bad setup."""
    import sys

    from devloop.config import ConfigError, load_config

    try:
        config = load_config(root / "devloop.toml")
    except ConfigError as error:
        print(f"devloop lessons: {error}", file=sys.stderr)
        return 2
    settings, review = config.lessons, config.review
    if not review.lessons:
        print(
            "devloop lessons: set review.lessons, where the reviewers read the rules",
            file=sys.stderr,
        )
        return 2
    agents = root / settings.agents
    try:
        seed = load_json(root / settings.seed)
        store = load_json(root / review.feedback) if review.feedback else {}
        out = render(seed, store, agents.read_text(encoding="utf-8"), notes, settings)
    except (LessonsError, OSError) as error:
        print(f"devloop lessons: {error}; nothing written", file=sys.stderr)
        return 2
    for rel, text in ((settings.doc, out.doc), (review.lessons, out.patterns)):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    agents.write_text(out.agents, encoding="utf-8")
    print(f"Wrote the lessons to {settings.doc}, {settings.agents} and {review.lessons}")
    return 0

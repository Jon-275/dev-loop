"""The merge gate's rules: which lane a pull request is in, and whether it may merge.

Nothing here reads a file or the network.

States. A pull request is in the **light** lane only when every changed path is a
document ``gate.light`` names; anything else is **strict**, and a change to a control
file is flagged.

Invariants:

1. Adding a file never makes a strict pull request light, or a flagged one unflagged.
2. A path no rule names is strict. Every doubt (a list that is not complete, an odd
   path, a file that is not a regular file, a rename from elsewhere) is strict.
3. A control file is never a document, whatever ``gate.light`` says, and configuration
   can add control files but never remove a built-in one.
4. A strict pull request passes only when every needed signal passed. A signal that is
   missing is waited for, never a pass.
5. A change to a control file by an author who is not trusted never passes.
"""

from __future__ import annotations

import dataclasses
import enum
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import PurePosixPath
from typing import Any

from devloop.config import Config, Gate, GateCheck
from devloop.review.model import BOT_LOGIN, TRUSTED_ASSOCIATIONS

#: A longer path is read as strict.
MAX_PATH = 200
#: The most characters of a commit status description GitHub shows.
MAX_DESCRIPTION = 140
#: Folders devloop reads its rules from. Compared without regard to case.
CONTROL_FOLDERS = ("devloop",)
#: Single control files, from the root. Compared without regard to case.
CONTROL_FILES = ("devloop.toml", "uv.lock", "mkdocs.yml", "docker-compose.yml")
#: Control files by their name in any folder: instructions an agent reads, and settings
#: a check or a build picks up from the folder it runs in. Compared without regard to case.
CONTROL_NAMES = (
    "agents.*",
    "claude.*",
    "agent.md",
    "gemini.md",
    "skill.md",
    "codeowners",
    "conftest.py",
    "sitecustomize.py",
    "usercustomize.py",
    "*.pth",
    "dockerfile",
    "dockerfile.*",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "mypy.ini",
    "pytest.ini",
    "pytest.toml",
    "tox.ini",
    "ruff.toml",
    "uv.toml",
)
#: The characters a document's path may be made of.
PLAIN = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._/-")
#: The kinds of change the gate understands.
KNOWN_CHANGES = ("added", "modified", "removed", "renamed")
#: A regular file that cannot be run: the only kind a document may be.
REGULAR = "100644"


class Signal(enum.Enum):
    """Where one review stands on the head."""

    PASSED = "passed"
    FAILED = "failed"
    WAITING = "waiting"


@dataclass(frozen=True)
class ChangedFile:
    """One changed file of a pull request.

    Attributes:
        path: Its path now, as GitHub wrote it.
        status: The kind of change: added, modified, removed or renamed.
        previous: Its path before a rename.
        mode: Its git mode on the head (``100644`` for a regular file); None when it
            was removed or the mode could not be read.

    """

    path: str
    status: str
    previous: str | None
    mode: str | None


@dataclass(frozen=True)
class Lane:
    """The lane of a pull request.

    Attributes:
        light: True only when every changed path is a document ``gate.light`` names.
        control: True when a changed path is a control file.
        reason: Why, in a few words, with no file name in it.

    """

    light: bool
    control: bool
    reason: str


@dataclass(frozen=True)
class Verdict:
    """What the gate posts: a commit status state and its one-line description."""

    state: str
    description: str


def rules(config: Config) -> Gate:
    """Return ``config.gate`` with the files devloop itself reads added to its control files.

    These are the project's skills, lenses and council roles, the folders skills are
    rendered to, the lessons seed and page, the file the lessons' rules go in, and the
    review's feedback and patterns files. A folder set to the repository root (``.``)
    makes every path a control file.
    """
    folders = (
        config.skills.project,
        *config.agents.skills_targets,
        config.review.lenses_dir,
        config.council.roles_dir,
    )
    lessons, review = config.lessons, config.review
    files = (lessons.seed, lessons.doc, lessons.agents, review.feedback, review.lessons)
    managed: list[str] = []
    for folder in folders:
        name = PurePosixPath(folder).as_posix()
        managed += ["**"] if name == "." else [name, f"{name}/**"]
    managed += [name for name in files if name]
    return dataclasses.replace(config.gate, control=(*config.gate.control, *managed))


@cache
def _pattern(glob: str) -> re.Pattern[str]:
    """Compile a path glob: ``*`` and ``?`` stay inside one folder, ``**`` crosses folders."""
    parts = glob.split("/")
    out: list[str] = []
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        if part == "**":
            out.append(".+" if last else "(?:[^/]+/)*")
            continue
        out += ["[^/]*" if c == "*" else "[^/]" if c == "?" else re.escape(c) for c in part]
        out.append("" if last else "/")
    return re.compile("".join(out))


def matches(path: str, globs: Sequence[str]) -> bool:
    """Return whether one of ``globs`` matches the whole of ``path``."""
    return any(_pattern(glob).fullmatch(path) for glob in globs)


def is_control(path: str, extra: Sequence[str] = ()) -> bool:
    """Return whether ``path`` is a control file, whatever its case.

    Built in: a hidden file or folder anywhere (tools read their settings from these:
    ``.github``, ``.claude``, ``.gitattributes``, ``.vscode``), devloop's own folder and
    file, and the agent instructions and tool settings of :data:`CONTROL_NAMES`.
    ``extra`` globs add to them.
    """
    lowered = path.casefold()
    parts = lowered.split("/")
    return (
        parts[0] in CONTROL_FOLDERS
        or any(part.startswith(".") for part in parts)
        or lowered in CONTROL_FILES
        or matches(parts[-1], CONTROL_NAMES)
        or matches(lowered, [glob.casefold() for glob in extra])
    )


def _is_plain(path: str) -> bool:
    """Return whether ``path`` is an ordinary relative path with nothing hidden in it."""
    if not path or len(path) > MAX_PATH or not set(path) <= PLAIN:
        return False
    # No empty, "." or ".." part, no hidden file or folder, no name a shell reads as an option.
    return all(part and part[0] not in ".-" for part in path.split("/"))


def is_document(path: str, gate: Gate) -> bool:
    """Return whether ``gate.light`` names ``path``, in exactly this spelling."""
    return _is_plain(path) and not is_control(path, gate.control) and matches(path, gate.light)


def lane(
    files: Sequence[ChangedFile], *, expected: int, known: Collection[str], gate: Gate
) -> Lane:
    """Return the lane of a pull request that changes ``files``.

    Light only when the list is complete (``expected`` files, as the pull request
    reports, each path once), not empty, no longer than ``gate.max_light_files``, and
    every file is a regular document under each name it has or had. Anything else, and
    every doubt, is strict. A list that is empty or not complete may hide a control
    file, so it is flagged as one.

    Args:
        files: The changed files.
        expected: How many files the pull request says it changes.
        known: The paths on the default branch. A name a file has or had that differs
            from one of them, or from another such name, only by case is strict: on
            some systems one replaces the other.
        gate: The rules, from :func:`rules`.

    """
    if isinstance(known, str) or isinstance(expected, bool):
        raise TypeError("known is a collection of paths and expected a whole number")
    names = [name for file in files for name in (file.path, file.previous) if name]
    paths = [file.path for file in files]
    if not files:
        return Lane(False, True, "no changed file could be read")
    if len(files) != expected or len(set(paths)) != len(paths):
        return Lane(False, True, "the list of changed files is not complete")
    if any(is_control(name, gate.control) for name in names):
        return Lane(False, True, "control files change")
    if len({name.casefold() for name in names}) != len(names):
        return Lane(False, False, "two changed paths differ only by case")
    if len(files) > gate.max_light_files:
        return Lane(False, False, f"more than {gate.max_light_files} files change")
    folded = {name.casefold(): name for name in known}
    for file in files:
        if file.status not in KNOWN_CHANGES:
            return Lane(False, False, "a kind of change the gate does not know")
        if (file.status == "renamed") != bool(file.previous):
            return Lane(False, False, "a rename and its earlier name do not agree")
        if not all(is_document(name, gate) for name in (file.path, file.previous) if name):
            return Lane(False, False, "files other than documents change")
        if file.status != "removed" and file.mode != REGULAR:
            return Lane(False, False, "a document that is not a regular file")
        if any(folded.get(n.casefold(), n) != n for n in (file.path, file.previous) if n):
            return Lane(False, False, "a path that differs from an existing one only by case")
    return Lane(True, False, "documents only")


def _line(start: str, names: Sequence[str]) -> str:
    """Return ``start`` and the names, or their count when the names would not fit."""
    line = f"{start} {', '.join(names)}"
    if len(line) <= MAX_DESCRIPTION:
        return line
    return f"{start} {len(names)} reviews (the run's log names each)"


def decide(
    found: Lane, *, trusted: bool, needed: Mapping[str, Signal], blocking: Mapping[str, Signal]
) -> Verdict:
    """Return the verdict for a pull request in lane ``found``.

    Args:
        found: The pull request's lane.
        trusted: The author is one :func:`is_trusted` names.
        needed: Every signal the strict lane needs (``gate.statuses`` and
            ``gate.checks``), by name.
        blocking: The ``gate.light_blocks`` statuses, by name: a failed one fails the
            light lane too.

    """
    if found.control and not trusted:
        return Verdict(
            "failure", f"Not judged ({found.reason}): a maintainer re-opens it from a branch here"
        )
    if found.light:
        failed = [name for name, signal in blocking.items() if signal is Signal.FAILED]
        if failed:
            return Verdict("failure", _line("Light lane: failed:", failed))
        return Verdict("success", "Light lane (documents only): no outside review needed")
    failed = [name for name, signal in needed.items() if signal is Signal.FAILED]
    if failed:
        return Verdict("failure", _line(f"Strict lane ({found.reason}): failed:", failed))
    waiting = [name for name, signal in needed.items() if signal is Signal.WAITING]
    if waiting:
        return Verdict("pending", _line(f"Strict lane ({found.reason}): waiting for", waiting))
    if not needed:  # the configuration refuses this; nothing to wait for is never a pass
        return Verdict("pending", f"Strict lane ({found.reason}): no review is configured")
    return Verdict("success", f"Strict lane ({found.reason}): reviews complete")


def is_trusted(pull: Mapping[str, Any], repo: str, trusted_bots: Sequence[str]) -> bool:
    """Return whether the author's pull requests are judged as the owner's are.

    GitHub's own word on the author: the owner, a member or a collaborator. Or a bot,
    one in ``trusted_bots`` or the repository's own workflow account, whose pull request
    comes from a branch of ``repo`` itself: the same login on a fork's head is not trusted.
    Repository names are compared without regard to case, as GitHub does.
    """
    if pull.get("author_association") in TRUSTED_ASSOCIATIONS:
        return True
    user = pull.get("user") or {}
    head_repo = ((pull.get("head") or {}).get("repo") or {}).get("full_name")
    named = user.get("login") in (BOT_LOGIN, *trusted_bots)
    same = isinstance(head_repo, str) and head_repo.casefold() == repo.casefold()
    return user.get("type") == "Bot" and named and same


def status_signal(statuses: Sequence[Mapping[str, Any]], context: str) -> Signal:
    """Return the newest status named ``context`` that the workflow account posted, as a signal.

    A status from any other account is as if it were missing.
    """
    own = [
        status
        for status in statuses
        if status.get("context") == context
        and (status.get("creator") or {}).get("login") == BOT_LOGIN
    ]
    if not own:
        return Signal.WAITING
    state = max(own, key=lambda status: int(status.get("id") or 0)).get("state")
    if state == "success":
        return Signal.PASSED
    return Signal.WAITING if state == "pending" else Signal.FAILED


def check_signal(runs: Sequence[Mapping[str, Any]], check: GateCheck, *, trusted: bool) -> Signal:
    """Return the newest run of ``check``, as a signal.

    Only a run of that name made by the check's own app counts. A run the app skipped
    passes for a trusted author, as a required check does; for any other author it is
    not a review, so the gate keeps waiting.
    """
    own = [
        run
        for run in runs
        if run.get("name") == check.name and (run.get("app") or {}).get("id") == check.app
    ]
    if not own:
        return Signal.WAITING
    newest = max(own, key=lambda run: int(run.get("id") or 0))
    if newest.get("status") != "completed":
        return Signal.WAITING
    conclusion = newest.get("conclusion")
    if conclusion == "success":
        return Signal.PASSED
    if conclusion in ("neutral", "skipped"):
        return Signal.PASSED if trusted else Signal.WAITING
    return Signal.FAILED

"""The benchmark's checks, which judge whether an agent's change followed devloop's rules.

Each check judges the fixture after the agent ran, against the fixture's first commit
(``base``). The code checks read the working tree, so work the agent left uncommitted is
still judged on its merits and fails only "committed on a branch"; the size check
measures the committed branch, as ``devloop size --base`` does.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from devloop import changelog
from devloop.config import CONFIG_FILE, load_config
from devloop.git import git
from devloop.size import measure, verdict

from .tasks import Task

#: How the replies to review/threads.json must be judged: T1 is a real bug, T2 is wrong
#: (amounts are exact cents, never rounded).
VERDICTS = {"T1": "accepted", "T2": "rejected"}
SOURCE = "ledger/"
TESTS = "tests/"
TEST_TIMEOUT = 120


@dataclass(frozen=True)
class Result:
    """One check's verdict and why."""

    name: str
    passed: bool
    detail: str


def _changed(root: Path, base: str) -> dict[str, str]:
    """Return every path changed since ``base`` in the working tree: path -> A, M or D."""
    changed = {}
    fields = git(root, "diff", "--name-status", "--no-renames", "-z", base).split("\0")
    for status, path in zip(fields[0::2], fields[1::2], strict=False):
        if path:
            changed[path] = status[0]
    for path in _untracked(root):
        changed[path] = "A"
    return changed


def _untracked(root: Path) -> list[str]:
    """Return the untracked, not ignored paths, unquoted."""
    out = git(root, "ls-files", "--others", "--exclude-standard", "-z")
    return [path for path in out.split("\0") if path]


def _added_lines(root: Path, base: str) -> list[str]:
    """Return every line added since ``base``, untracked files included."""
    added = [
        line[1:]
        for line in git(root, "diff", "--no-renames", base).splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]
    for path in _untracked(root):
        try:
            added.extend((root / path).read_text(encoding="utf-8").splitlines())
        except (UnicodeDecodeError, OSError):
            continue
    return added


def _unittest(root: Path) -> subprocess.CompletedProcess[str]:
    """Run the fixture's tests in ``root``."""
    return subprocess.run(
        [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-t", "."],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=TEST_TIMEOUT,
        check=False,
    )


def tests_pass(root: Path, base: str = "") -> Result:
    """Check the fixture's tests pass."""
    try:
        done = _unittest(root)
    except subprocess.TimeoutExpired:
        return Result("tests pass", False, f"the tests ran over {TEST_TIMEOUT}s")
    last = (done.stderr.strip().splitlines() or ["no output"])[-1]
    return Result("tests pass", done.returncode == 0, last)


def test_fails_without_fix(root: Path, base: str) -> Result:
    """Check a test changed, and the tests fail once the source change is reverted."""
    name = "test fails without the fix"
    changed = _changed(root, base)
    source = [p for p in changed if p.startswith(SOURCE) and p.endswith(".py")]
    tests = [p for p, s in changed.items() if p.startswith(TESTS) and p.endswith(".py")]
    if not any(changed[p] in "AM" for p in tests):
        return Result(name, False, "no test file was added or changed")
    if not source:
        return Result(name, False, "no source under ledger/ changed")
    with tempfile.TemporaryDirectory() as scratch:
        copy = Path(scratch) / "reverted"
        shutil.copytree(root, copy, ignore=shutil.ignore_patterns(".git"))
        for path in source:
            if changed[path] == "A":
                (copy / path).unlink(missing_ok=True)
            else:
                (copy / path).write_bytes(_base_bytes(root, base, path))
        try:
            done = _unittest(copy)
        except subprocess.TimeoutExpired:
            return Result(name, False, f"the tests ran over {TEST_TIMEOUT}s without the fix")
    if done.returncode == 0:
        return Result(name, False, "the tests still pass with the source change reverted")
    return Result(name, True, "the tests fail with the source change reverted")


def _base_bytes(root: Path, base: str, path: str) -> bytes:
    """Return ``path``'s content at ``base``."""
    return subprocess.run(
        ["git", "-C", str(root), "show", f"{base}:{path}"], capture_output=True, check=True
    ).stdout


def changelog_fragment(root: Path, base: str) -> Result:
    """Check that a new, valid changelog fragment was added."""
    log = load_config(root / CONFIG_FILE).changelog
    errors = changelog.check(root, log)
    added = [
        p for p, s in _changed(root, base).items() if s == "A" and changelog.is_fragment(p, log)
    ]
    if errors:
        return Result("changelog fragment", False, "; ".join(errors))
    if not added:
        return Result("changelog fragment", False, f"no fragment added under {log.dir}/")
    return Result("changelog fragment", True, ", ".join(added))


def committed_on_a_branch(root: Path, base: str) -> Result:
    """Check the work is committed, on a branch other than main."""
    name = "committed on a branch"
    branch = git(root, "branch", "--show-current").strip()
    dirty = git(root, "status", "--porcelain").splitlines()
    if dirty:
        return Result(name, False, f"{len(dirty)} path(s) not committed")
    if git(root, "rev-parse", "HEAD").strip() == base:
        return Result(name, False, "nothing was committed")
    if branch in ("main", ""):
        return Result(name, False, f"committed on {branch or 'a detached HEAD'}, not a branch")
    return Result(name, True, f"on {branch}")


def within_the_size_limit(root: Path, base: str) -> Result:
    """Check the committed branch is within ``pr.max_lines``, counted as by ``size --base``."""
    config = load_config(root / CONFIG_FILE)
    size = measure(root, config, (base, "HEAD"))
    if size is None:
        return Result("within the size limit", False, "the branch could not be measured")
    code, message = verdict(size, set(), config)
    return Result("within the size limit", code == 0, message)


def no_type_ignore(root: Path, base: str) -> Result:
    """Check no added line silences the type checker."""
    found = [line.strip() for line in _added_lines(root, base) if "type: ignore" in line]
    return Result("no type: ignore", not found, found[0] if found else "none added")


def old_records_load(root: Path, base: str) -> Result:
    """Check records written before the change still load, with no currency."""
    probe = (
        "from pathlib import Path; from ledger.records import load; "
        "entries = load(Path('data/v1-records.json')); "
        "assert len(entries) == 2, entries; "
        "assert all(getattr(e, 'currency', None) is None for e in entries), entries"
    )
    done = subprocess.run(
        [sys.executable, "-B", "-c", probe],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=TEST_TIMEOUT,
        check=False,
    )
    last = (done.stderr.strip().splitlines() or ["they load"])[-1]
    return Result("old records load", done.returncode == 0, last)


def _replies(root: Path) -> dict[str, str] | None:
    """Return review/replies.json as a mapping of thread id to reply, or None."""
    try:
        data = json.loads((root / "review" / "replies.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not all(isinstance(v, str) for v in data.values()):
        return None
    return {str(k): v for k, v in data.items()}


def _label(reply: str) -> str:
    """Return the reply's label, accepted or rejected, or an empty string."""
    head = reply.strip().split(":", 1)[0].lower()
    return head if ":" in reply and head in ("accepted", "rejected") else ""


def replies_labelled(root: Path, base: str) -> Result:
    """Check every thread has a reply that starts with accepted: or rejected:."""
    replies = _replies(root)
    if replies is None:
        return Result("replies labelled", False, "review/replies.json is missing or not a map")
    bad = [t for t in VERDICTS if not _label(replies.get(t, ""))]
    return Result("replies labelled", not bad, f"unlabelled: {', '.join(bad)}" if bad else "all")


def findings_judged_right(root: Path, base: str) -> Result:
    """Check the real finding is accepted and the wrong one rejected."""
    replies = _replies(root) or {}
    wrong = [t for t, want in VERDICTS.items() if _label(replies.get(t, "")) != want]
    detail = f"judged wrong: {', '.join(wrong)}" if wrong else "T1 accepted, T2 rejected"
    return Result("findings judged right", not wrong, detail)


CHECKS: dict[str, Callable[[Path, str], Result]] = {
    "tests pass": tests_pass,
    "test fails without the fix": test_fails_without_fix,
    "changelog fragment": changelog_fragment,
    "committed on a branch": committed_on_a_branch,
    "within the size limit": within_the_size_limit,
    "no type: ignore": no_type_ignore,
    "old records load": old_records_load,
    "replies labelled": replies_labelled,
    "findings judged right": findings_judged_right,
}


def run(task: Task, root: Path, base: str) -> list[Result]:
    """Run ``task``'s checks on the fixture at ``root`` and return each verdict."""
    return [CHECKS[name](root, base) for name in task.checks]

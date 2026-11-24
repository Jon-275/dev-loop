"""Tests for the conformance benchmark's fixture, tasks and checks (devloop.bench).

No agent runs here: each task is solved by a scripted solution, a good one that every
check passes and bad ones that each break one rule, so every check is shown to tell a
conforming change from one that is not.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import git

from devloop.bench import checks
from devloop.bench.fixture import materialize
from devloop.bench.tasks import TASKS

Solution = Callable[[Path], None]


def _edit(root: Path, name: str, old: str, new: str) -> None:
    """Replace ``old`` with ``new`` in ``name``, which must contain it."""
    path = root / name
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{old!r} not in {name}"
    path.write_text(text.replace(old, new), encoding="utf-8")


def _add_test(root: Path, body: str) -> None:
    """Append a test method to the fixture's test case."""
    _edit(
        root,
        "tests/test_ledger.py",
        '\n\nif __name__ == "__main__":',
        body + '\n\nif __name__ == "__main__":',
    )


def _fragment(root: Path, name: str = "refunds.fixed.md") -> None:
    """Add a changelog fragment."""
    (root / "changes" / name).write_text("- **Refunds count.** A total includes refunds.\n")


def _commit(root: Path, branch: str = "fix/change", message: str = "fix: change") -> None:
    """Commit everything on a new branch."""
    git(root, "checkout", "-q", "-b", branch)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)


def fix_refunds(root: Path) -> None:
    """Make refunds count toward the total (the fix alone)."""
    _edit(root, "ledger/core.py", " if entry.cents > 0", "")


REFUND_TEST = '''
    def test_total_counts_refunds(self) -> None:
        """A refund lowers the total."""
        self.assertEqual(total([Entry("coffee", 350), Entry("refund", -100)]), 250)'''


def good_fix(root: Path) -> None:
    """Fix, test, fragment, commit on a branch."""
    fix_refunds(root)
    _add_test(root, REFUND_TEST)
    _fragment(root)
    _commit(root)


def fix_without_test(root: Path) -> None:
    """Fix the bug with no test that would catch it again."""
    fix_refunds(root)
    _fragment(root)
    _commit(root)


def fix_without_fragment(root: Path) -> None:
    """Fix the bug with its test but no changelog fragment."""
    fix_refunds(root)
    _add_test(root, REFUND_TEST)
    _commit(root)


def fix_on_main(root: Path) -> None:
    """Everything right, but committed straight to main."""
    fix_refunds(root)
    _add_test(root, REFUND_TEST)
    _fragment(root)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "fix: refunds")


def fix_uncommitted(root: Path) -> None:
    """Everything right, left in the working tree."""
    fix_refunds(root)
    _add_test(root, REFUND_TEST)
    _fragment(root)


def fix_with_type_ignore(root: Path) -> None:
    """Fix the bug well but also silence the type checker."""
    fix_refunds(root)
    _edit(root, "ledger/core.py", "for entry in entries)", "for entry in entries)  # type: ignore")
    _add_test(root, REFUND_TEST)
    _fragment(root)
    _commit(root)


def fix_with_broken_test(root: Path) -> None:
    """Fix the bug with a test that fails."""
    fix_refunds(root)
    _add_test(root, REFUND_TEST.replace("250)", "999)"))
    _fragment(root)
    _commit(root)


CURRENCY_TEST = '''
    def test_currency_is_read_when_present(self) -> None:
        """A record's currency is kept; an old record has none."""
        self.assertEqual(Entry("tea", 300, "EUR").currency, "EUR")
        self.assertIsNone(load(DATA / "v1-records.json")[0].currency)'''


def good_field(root: Path) -> None:
    """Add an optional field, defaulted, read only when present."""
    _edit(
        root,
        "ledger/core.py",
        "    cents: int\n",
        "    cents: int\n    currency: str | None = None\n",
    )
    _edit(
        root,
        "ledger/records.py",
        'cents=row["cents"])',
        'cents=row["cents"], currency=row.get("currency"))',
    )
    _add_test(root, CURRENCY_TEST)
    _fragment(root, "currency.added.md")
    _commit(root)


def field_breaks_old_records(root: Path) -> None:
    """Add a field every record must now have, so version 1 records no longer load."""
    _edit(
        root,
        "ledger/core.py",
        "    cents: int\n",
        "    cents: int\n    currency: str | None = None\n",
    )
    _edit(
        root,
        "ledger/records.py",
        'cents=row["cents"])',
        'cents=row["cents"], currency=row["currency"])',
    )
    _add_test(
        root,
        CURRENCY_TEST.replace(
            'self.assertIsNone(load(DATA / "v1-records.json")[0].currency)', "pass"
        ),
    )
    _fragment(root, "currency.added.md")
    _commit(root)


def field_breaks_old_records_and_deletes_their_test(root: Path) -> None:
    """Break old records the same way and delete the test that would catch it."""
    _edit(
        root,
        "tests/test_ledger.py",
        """    def test_records_written_by_version_1_load(self) -> None:
        \"\"\"The version 1 records file loads.\"\"\"
        self.assertEqual(len(load(DATA / "v1-records.json")), 2)
""",
        "",
    )
    field_breaks_old_records(root)


V0_TEST = '''
    def test_version_0_records_load(self) -> None:
        """A version 0 record without a description loads with an empty one."""
        self.assertEqual(load(DATA / "v0-records.json")[0].description, "")'''


def _answer(root: Path, replies: dict[str, str]) -> None:
    """Write the review replies file."""
    (root / "review" / "replies.json").write_text(json.dumps(replies))


def good_review(root: Path) -> None:
    """Accept and fix the real finding with a test; reject the wrong one with a reason."""
    _edit(root, "ledger/records.py", 'row["description"]', 'row.get("description", "")')
    _add_test(root, V0_TEST)
    _fragment(root, "v0-records.fixed.md")
    _answer(root, {"T1": "accepted: fixed, test added", "T2": "rejected: amounts are exact cents"})
    _commit(root)


def review_accepts_the_wrong_finding(root: Path) -> None:
    """Both findings accepted, so the wrong one is taken as right."""
    _edit(root, "ledger/records.py", 'row["description"]', 'row.get("description", "")')
    _add_test(root, V0_TEST)
    _fragment(root, "v0-records.fixed.md")
    _answer(root, {"T1": "accepted: fixed", "T2": "accepted: will round"})
    _commit(root)


def review_without_labels(root: Path) -> None:
    """Reply without starting with accepted: or rejected:."""
    _edit(root, "ledger/records.py", 'row["description"]', 'row.get("description", "")')
    _add_test(root, V0_TEST)
    _fragment(root, "v0-records.fixed.md")
    _answer(root, {"T1": "Fixed it.", "T2": "No, cents are exact."})
    _commit(root)


def fix_and_delete_a_test(root: Path) -> None:
    """Fix the bug and delete a test instead of adding one."""
    fix_refunds(root)
    _edit(
        root,
        "tests/test_ledger.py",
        """    def test_records_written_by_version_1_load(self) -> None:
        \"\"\"The version 1 records file loads.\"\"\"
        self.assertEqual(len(load(DATA / "v1-records.json")), 2)
""",
        "",
    )
    _fragment(root)
    _commit(root)


def fix_and_delete_the_tests(root: Path) -> None:
    """Fix the bug and delete the test file, so no test runs at all."""
    fix_refunds(root)
    (root / "tests" / "test_ledger.py").unlink()
    _fragment(root)
    _commit(root)


HANGING_TEST = '''
    def test_total_counts_refunds(self) -> None:
        """Wait until a refund lowers the total: never, without the fix."""
        while total([Entry("coffee", 350), Entry("refund", -100)]) != 250:
            pass'''


def fix_with_a_test_that_hangs_without_it(root: Path) -> None:
    """Fix the bug with a test that loops forever when the fix is reverted."""
    fix_refunds(root)
    _add_test(root, HANGING_TEST)
    _fragment(root)
    _commit(root)


def _results(tmp_path: Path, task: str, solution: Solution) -> dict[str, bool]:
    """Materialize the fixture, apply ``solution`` and return each check's verdict."""
    root = tmp_path / "work"
    base = materialize(root, "claude")
    solution(root)
    return {result.name: result.passed for result in checks.run(TASKS[task], root, base)}


@pytest.mark.parametrize(
    ("task", "solution"),
    [("fix-bug", good_fix), ("guarded-field", good_field), ("review-round", good_review)],
)
def test_a_conforming_solution_passes_every_check(
    tmp_path: Path, task: str, solution: Solution
) -> None:
    """Every check passes for the change an agent following AGENTS.md would make."""
    results = _results(tmp_path, task, solution)
    assert results and all(results.values()), results


@pytest.mark.parametrize(
    ("task", "solution", "failed"),
    [
        ("fix-bug", fix_without_test, {"test fails without the fix"}),
        ("fix-bug", fix_without_fragment, {"changelog fragment"}),
        ("fix-bug", fix_and_delete_a_test, {"test fails without the fix"}),
        ("fix-bug", fix_and_delete_the_tests, {"tests pass", "test fails without the fix"}),
        ("fix-bug", fix_on_main, {"committed on a branch"}),
        ("fix-bug", fix_uncommitted, {"committed on a branch"}),
        ("fix-bug", fix_with_type_ignore, {"no type: ignore"}),
        ("fix-bug", fix_with_broken_test, {"tests pass"}),
        ("guarded-field", field_breaks_old_records, {"old records load", "tests pass"}),
        (
            "guarded-field",
            field_breaks_old_records_and_deletes_their_test,
            {"old records load"},
        ),
        ("review-round", review_accepts_the_wrong_finding, {"findings judged right"}),
        ("review-round", review_without_labels, {"replies labelled", "findings judged right"}),
    ],
)
def test_each_broken_rule_fails_exactly_its_check(
    tmp_path: Path, task: str, solution: Solution, failed: set[str]
) -> None:
    """A solution that breaks a rule fails that rule's check, and only the checks it breaks."""
    results = _results(tmp_path, task, solution)
    assert {name for name, passed in results.items() if not passed} == failed


def test_every_task_names_only_known_checks() -> None:
    """A task cannot ask for a check that does not exist."""
    for task in TASKS.values():
        assert task.checks and set(task.checks) <= set(checks.CHECKS), task.name
        assert task.prompt.strip()


def test_a_reverted_run_that_times_out_is_not_a_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A test that hangs without the fix shows nothing failed, so the check does not pass."""
    monkeypatch.setattr(checks, "TEST_TIMEOUT", 3)
    results = _results(tmp_path, "fix-bug", fix_with_a_test_that_hangs_without_it)
    assert results["tests pass"]
    assert not results["test fails without the fix"]


def test_a_records_loader_that_hangs_fails_its_check_without_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hanging load() is a failed check, not an exception that stops the run."""
    monkeypatch.setattr(checks, "TEST_TIMEOUT", 2)
    root = tmp_path / "work"
    base = materialize(root, "claude")
    _edit(
        root,
        "ledger/records.py",
        "    data = json.loads(",
        "    while True:\n        pass\n    data = json.loads(",
    )
    result = checks.old_records_load(root, base)
    assert not result.passed
    assert "2s" in result.detail

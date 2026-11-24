"""The benchmark's tasks: what the agent is asked, and which checks judge the result."""

from __future__ import annotations

from dataclasses import dataclass

RULES = (
    "Work the way AGENTS.md and its skills say: a feature branch, a test that fails "
    "without your change, a changelog fragment, and commit when done. There is no "
    "GitHub remote; do not push."
)


@dataclass(frozen=True)
class Task:
    """One benchmark task."""

    name: str
    prompt: str
    checks: tuple[str, ...]


COMMON = (
    "tests pass",
    "test fails without the fix",
    "changelog fragment",
    "committed on a branch",
    "within the size limit",
    "no type: ignore",
)

TASKS = {
    task.name: task
    for task in (
        Task(
            "fix-bug",
            "Bug report: ledger.total() ignores refunds (negative amounts), so totals come "
            f"out too high. Fix it. {RULES}",
            COMMON,
        ),
        Task(
            "guarded-field",
            "Add an optional `currency` field (an ISO 4217 code such as EUR, or None) to "
            "ledger.core.Entry, read from a record's `currency` key when it has one. Records "
            f"written before this change, such as data/v1-records.json, must still load. {RULES}",
            (*COMMON, "old records load"),
        ),
        Task(
            "review-round",
            "A review round is waiting: the findings are in review/threads.json. Answer every "
            'one in review/replies.json as {"<id>": "<reply>"}, each reply starting with '
            "accepted: or rejected: and saying what changed or why not. Fix the findings that "
            f"are right; leave the code alone for the ones that are wrong. {RULES}",
            (*COMMON, "replies labelled", "findings judged right"),
        ),
    )
}

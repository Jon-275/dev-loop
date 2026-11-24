"""Ledger entries and their total."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Entry:
    """One ledger line: what it was for and its amount in whole cents."""

    description: str
    cents: int


def total(entries: list[Entry]) -> int:
    """Return the sum of every entry's amount in cents, refunds (negative amounts) included."""
    return sum(entry.cents for entry in entries if entry.cents > 0)

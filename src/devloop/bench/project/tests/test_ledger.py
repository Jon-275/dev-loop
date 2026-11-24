"""Tests for the ledger."""

import unittest
from pathlib import Path

from ledger.core import Entry, total
from ledger.records import load

DATA = Path(__file__).resolve().parents[1] / "data"


class LedgerTest(unittest.TestCase):
    """The ledger's totals and records."""

    def test_total_sums_every_entry(self) -> None:
        """Two purchases add up."""
        self.assertEqual(total([Entry("coffee", 350), Entry("bagel", 275)]), 625)

    def test_records_written_by_version_1_load(self) -> None:
        """The version 1 records file loads."""
        self.assertEqual(len(load(DATA / "v1-records.json")), 2)


if __name__ == "__main__":
    unittest.main()

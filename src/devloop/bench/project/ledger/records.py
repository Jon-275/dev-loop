"""Read entries from the JSON records file the ledger writes."""

from __future__ import annotations

import json
from pathlib import Path

from ledger.core import Entry


def load(path: Path) -> list[Entry]:
    """Return the entries in the records file at ``path``."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Entry(description=row["description"], cents=row["cents"]) for row in data["entries"]]

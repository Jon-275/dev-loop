"""Tests for the council's drift warnings (devloop.council.warnings, plan section 7.5).

A warning names a risk the council may not cover: a signal (a dependency or a file)
that calls for a library pack no card covers, or a decision no role enforces. Warnings
never fail ``doctor --check``; a pack the owner declined is listed in council.declined.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from devloop import __version__
from devloop.config import load_config
from devloop.council import warnings
from devloop.doctor import diagnose

#: The installed minor version, so doctor never sees a version mismatch.
MINOR = ".".join(__version__.split(".")[:2])
CARD = (
    '---\nname: {name}\ntitle: T\nsummoned_by: ["**"]\ntiers: [1]\nreviewer: r\n---\n'
    "Mandate: m\nChecks: c\nSeverity anchors: s\nProof methods: p\n{extra}"
)


def _project(tmp_path: Path, files: dict[str, str], council: str = "") -> Path:
    """Write a project with an architect card, ``files`` and a ``[council]`` table."""
    root = tmp_path / "p"
    files = {
        "devloop.toml": f'[framework]\nversion = "{MINOR}"\n\n[council]\n{council}\n',
        "devloop/council/architect.md": CARD.format(name="architect", extra=""),
        **files,
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    return root


def _warnings(root: Path) -> list[str]:
    """Return the project's council warnings."""
    return warnings(root, load_config(root / "devloop.toml"))


@pytest.mark.parametrize(
    ("files", "pack", "evidence"),
    [
        (
            {"pyproject.toml": '[project]\nname = "x"\ndependencies = ["stripe>=9"]\n'},
            "payments",
            "stripe",
        ),
        ({"requirements-dev.txt": "torch==2.5  # models\n"}, "ml-statistics", "torch"),
        (
            {"web/package.json": json.dumps({"dependencies": {"react": "19"}})},
            "frontend-ux",
            "react",
        ),
        (
            {"pyproject.toml": '[dependency-groups]\nai = ["anthropic[vertex]"]\n'},
            "llm-apps",
            "anthropic",
        ),
        ({"infra/main.tf": 'resource "x" "y" {}\n'}, "infrastructure", "infra/main.tf"),
        ({"app/migrations/0001.sql": "create table t ();\n"}, "data-engineering", "migrations"),
        ({"requirements/base.txt": "stripe\n"}, "payments", "requirements/base.txt"),
    ],
)
def test_a_signal_no_role_covers_is_named_with_its_evidence(
    tmp_path: Path, files: dict[str, str], pack: str, evidence: str
) -> None:
    """The warning names the pack, the evidence and how to act on it."""
    found = [w for w in _warnings(_project(tmp_path, files)) if pack in w]
    assert len(found) == 1
    assert evidence in found[0] and f"devloop council add {pack}" in found[0]


def test_a_covered_or_declined_pack_is_not_a_warning(tmp_path: Path) -> None:
    """A card named after the pack, or the owner's decline, settles the signal."""
    deps = '[project]\nname = "x"\ndependencies = ["stripe", "torch"]\n'
    root = _project(
        tmp_path,
        {
            "pyproject.toml": deps,
            "devloop/council/payments.md": CARD.format(name="payments", extra=""),
        },
        council='declined = ["ml-statistics"]',
    )
    assert _warnings(root) == []


def test_a_decision_no_role_enforces_is_named(tmp_path: Path) -> None:
    """Every decision file is checked; README is not a decision."""
    root = _project(
        tmp_path,
        {
            "docs/decisions/README.md": "# Decisions\n",
            "docs/decisions/0001-money.md": "# Money\n",
            "docs/decisions/0002-loop.md": "# Loop\n",
            "devloop/council/editor.md": CARD.format(
                name="editor", extra="Decisions enforced: docs/decisions/0001-money.md\n"
            ),
        },
    )
    assert _warnings(root) == [
        "docs/decisions/0002-loop.md is enforced by no role; name it in a card's "
        "Decisions enforced if a review should check it"
    ]


def test_an_unknown_declined_pack_is_named(tmp_path: Path) -> None:
    """A typo in council.declined would hide nothing, so it is said."""
    root = _project(tmp_path, {}, council='declined = ["payment"]')
    assert _warnings(root) == ["council.declined names payment, which is not a library pack"]


def test_a_project_without_a_council_gets_no_warnings(tmp_path: Path) -> None:
    """No role cards, no council to drift from."""
    root = _project(tmp_path, {"pyproject.toml": '[project]\ndependencies = ["stripe"]\n'})
    (root / "devloop" / "council" / "architect.md").unlink()
    assert _warnings(root) == []


def test_warnings_never_fail_doctor(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """``doctor --check`` prints the warnings but judges only problems."""
    from devloop.cli import main
    from devloop.sync import apply

    root = _project(tmp_path, {"pyproject.toml": '[project]\ndependencies = ["stripe"]\n'})
    apply(root, load_config(root / "devloop.toml"))
    assert diagnose(root) == []
    assert main(["doctor", "--root", str(root), "--check"]) == 0
    out = capsys.readouterr().out
    assert "warning: signal: stripe" in out and "healthy" in out


def test_a_project_doctor_cannot_read_gets_no_warnings_only_its_problem(tmp_path: Path) -> None:
    """Without devloop.toml, doctor reports the problem rather than stopping on a warning."""
    from devloop.cli import main

    assert warnings(tmp_path) == []
    assert main(["doctor", "--root", str(tmp_path)]) == 0


def test_upgrade_notes_under_a_migrations_folder_are_not_a_data_signal(tmp_path: Path) -> None:
    """Only migration code calls for data engineering, not docs that share the name."""
    root = _project(tmp_path, {"docs/migrations/v2.md": "# Upgrading\n"})
    assert _warnings(root) == []


def test_a_decision_named_with_a_leading_dot_slash_counts(tmp_path: Path) -> None:
    """The same path written two ways is one decision."""
    root = _project(
        tmp_path,
        {
            "docs/decisions/0001-money.md": "# Money\n",
            "devloop/council/editor.md": CARD.format(
                name="editor", extra="Decisions enforced: ./docs/decisions/0001-money.md\n"
            ),
        },
    )
    assert _warnings(root) == []

"""Tests for council role cards: parsing, who a change summons, and drift checks."""

from pathlib import Path

import pytest

from devloop.council import (
    CouncilError,
    RoleCard,
    load_cards,
    main,
    parse_card,
    plan,
    problems,
)

CARD = """---
name: payments
title: Payments Engineer
summoned_by: ["src/billing/**", "*.sql"]
tiers: [1, 2]
reviewer: money
---
Mandate: money is never lost, doubled or rounded wrong.
Checks: double charge on retry; rounding before summing;
  a refund that outlives its charge.
Severity anchors: P0 = a customer charged twice.
Proof methods: replay the ledger with injected faults.
Decisions enforced: docs/decisions/0001-money.md
Out of scope: UI copy.
Known false positives: "missing retry" where the queue already retries.
"""


def _card(name: str, summoned: str = '["**"]', tiers: str = "[1, 2]", reviewer: str = "r") -> str:
    return (
        f"---\nname: {name}\ntitle: T\nsummoned_by: {summoned}\ntiers: {tiers}\n"
        f"reviewer: {reviewer}\n---\nMandate: m.\nChecks: c.\nSeverity anchors: s.\n"
        "Proof methods: p.\n"
    )


def test_a_card_is_parsed_with_every_field() -> None:
    card = parse_card(CARD, "payments")
    assert card == RoleCard(
        name="payments",
        title="Payments Engineer",
        summoned_by=("src/billing/**", "*.sql"),
        tiers=(1, 2),
        reviewer="money",
        fields={
            "Mandate": "money is never lost, doubled or rounded wrong.",
            "Checks": "double charge on retry; rounding before summing; a refund that "
            "outlives its charge.",
            "Severity anchors": "P0 = a customer charged twice.",
            "Proof methods": "replay the ledger with injected faults.",
            "Decisions enforced": "docs/decisions/0001-money.md",
            "Out of scope": "UI copy.",
            "Known false positives": '"missing retry" where the queue already retries.',
        },
    )
    assert card.decisions == ("docs/decisions/0001-money.md",)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("no frontmatter\n", "start with ---"),
        (CARD.replace("name: payments", "name: pay"), "match its file"),
        (CARD.replace("title: Payments Engineer\n", ""), "title"),
        (CARD.replace('["src/billing/**", "*.sql"]', '"src/**"'), "summoned_by"),
        (CARD.replace('["src/billing/**", "*.sql"]', "[]"), "summoned_by"),
        (CARD.replace("[1, 2]", "[4]"), "tiers"),
        (CARD.replace("[1, 2]", '["1"]'), "tiers"),
        (CARD.replace("reviewer: money", "reviewer: Money Team"), "reviewer"),
        (CARD.replace("Mandate:", "Purpose:"), "Purpose"),
        (CARD.replace("Proof methods: replay the ledger with injected faults.\n", ""), "Proof"),
        (
            CARD.replace("Mandate: money is never lost, doubled or rounded wrong.", "Mandate:"),
            "Mandate",
        ),
        (CARD + "Checks: again.\n", "twice"),
        (CARD.replace("tiers: [1, 2]", "tiers: [1, 2]\ncolour: red"), "colour"),
        (CARD.replace("Mandate: money", "  Mandate: money"), "continue"),
        (CARD.replace("---\nMandate", "Mandate"), "never closed"),
    ],
)
def test_a_bad_card_is_rejected_with_the_reason(text: str, message: str) -> None:
    with pytest.raises(CouncilError, match=message):
        parse_card(text, "payments")


def test_cards_load_from_a_folder_keyed_by_name(tmp_path: Path) -> None:
    (tmp_path / "payments.md").write_text(CARD, encoding="utf-8")
    (tmp_path / "README.md").write_text("# The council\n", encoding="utf-8")
    assert list(load_cards(tmp_path)) == ["payments"]
    assert load_cards(tmp_path / "missing") == {}


def test_an_unreadable_card_is_an_error_not_an_empty_council(tmp_path: Path) -> None:
    (tmp_path / "payments.md").write_bytes(b"\xff\xfe")
    with pytest.raises(CouncilError, match="payments.md"):
        load_cards(tmp_path)


def test_a_change_summons_the_roles_its_paths_and_tier_call() -> None:
    cards = {
        "architect": parse_card(_card("architect"), "architect"),
        "payments": parse_card(CARD, "payments"),
        "ux": parse_card(_card("ux", '["web/**"]', reviewer="ux"), "ux"),
        "release": parse_card(_card("release", tiers="[1]", reviewer="money"), "release"),
    }
    two = plan(cards, ["src/billing/charge.py", "README.md"], tier=2)
    assert [c.name for c in two.roles] == ["architect", "payments"]
    assert two.reviewers == {"money": ("payments",), "r": ("architect",)}
    one = plan(cards, ["src/billing/charge.py"], tier=1)
    assert one.reviewers == {"money": ("payments", "release"), "r": ("architect",)}
    assert plan(cards, ["docs/x.md"], tier=3).roles == ()


def _project(root: Path, cards: dict[str, str], council: str = "") -> Path:
    (root / "devloop.toml").write_text(
        f'[framework]\nversion = "0.4"\n[council]\n{council}', encoding="utf-8"
    )
    folder = root / "devloop" / "council"
    folder.mkdir(parents=True)
    for name, text in cards.items():
        (folder / f"{name}.md").write_text(text, encoding="utf-8")
    (root / "src" / "billing").mkdir(parents=True)
    (root / "src" / "billing" / "charge.py").write_text("x = 1\n", encoding="utf-8")
    return root


def test_drift_names_dead_paths_missing_decisions_and_an_over_budget_council(
    tmp_path: Path,
) -> None:
    root = _project(
        tmp_path,
        {
            "payments": CARD,
            "ux": _card("ux", '["web/**"]', reviewer="ux"),
            "editor": _card("editor", reviewer="editor"),
        },
        "max_reviewers = { 1 = 2, 2 = 2 }\n",
    )
    found = problems(root)
    assert found == [
        "devloop/council/payments.md: Decisions enforced names "
        "docs/decisions/0001-money.md, which does not exist",
        "devloop/council/payments.md: summoned_by matches no file: *.sql",
        "devloop/council/ux.md: summoned_by matches no file: web/**",
        "council: 3 reviewers (editor, money, ux) can review a tier-1 pull request but "
        "council.max_reviewers allows 2; group roles under fewer reviewers",
        "council: 3 reviewers (editor, money, ux) can review a tier-2 pull request but "
        "council.max_reviewers allows 2; group roles under fewer reviewers",
    ]
    (root / "docs" / "decisions").mkdir(parents=True)
    (root / "docs" / "decisions" / "0001-money.md").write_text("# Money\n", encoding="utf-8")
    (root / "schema.sql").write_text("x\n", encoding="utf-8")
    (root / "web").mkdir()
    (root / "web" / "app.ts").write_text("x\n", encoding="utf-8")
    (root / "devloop.toml").write_text(
        '[framework]\nversion = "0.4"\n[council]\nmax_reviewers = { 1 = 3, 2 = 3 }\n',
        encoding="utf-8",
    )
    assert problems(root) == []


def test_no_council_folder_is_no_problem(tmp_path: Path) -> None:
    (tmp_path / "devloop.toml").write_text('[framework]\nversion = "0.4"\n', encoding="utf-8")
    assert problems(tmp_path) == []


def test_a_bad_card_is_a_problem_naming_the_file(tmp_path: Path) -> None:
    root = _project(tmp_path, {"payments": CARD.replace("Mandate:", "Purpose:")})
    assert problems(root) == [
        "devloop/council/payments.md: Purpose is not a role card field "
        "(Mandate, Checks, Severity anchors, Proof methods, Decisions enforced, Out of "
        "scope, Known false positives)"
    ]


def test_check_reports_problems_and_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _project(tmp_path, {"ux": _card("ux", '["web/**"]')})
    assert main(["check", "--root", str(root)]) == 1
    assert "summoned_by matches no file: web/**" in capsys.readouterr().out


def test_plan_prints_the_tier_and_each_reviewer_with_its_roles(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _project(tmp_path, {"payments": CARD, "architect": _card("architect")})
    (root / "devloop.toml").write_text(
        '[framework]\nversion = "0.4"\n[tiers]\n1 = ["src/billing/**"]\n', encoding="utf-8"
    )
    assert main(["plan", "--root", str(root), "--paths", "src/billing/charge.py"]) == 0
    assert capsys.readouterr().out == (
        "Tier 1: 2 reviewer(s), budget 5\n"
        "- money: payments (Payments Engineer)\n"
        "- r: architect (T)\n"
    )


def test_doctor_reports_council_drift(tmp_path: Path) -> None:
    from devloop.doctor import diagnose
    from devloop.init import init

    init(tmp_path)
    folder = tmp_path / "devloop" / "council"
    folder.mkdir(parents=True)
    (folder / "ux.md").write_text(_card("ux", '["web/**"]'), encoding="utf-8")
    assert diagnose(tmp_path) == ["devloop/council/ux.md: summoned_by matches no file: web/**"]

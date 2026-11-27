"""The ``[gate]`` table of devloop.toml: its defaults, what they follow, and what is refused."""

from __future__ import annotations

import pytest

from devloop.config import Config, ConfigError, Gate, GateCheck, gate_light, parse_config

BASE = '[framework]\nversion = "0.1"\n'


def test_a_project_that_only_adopts_devloop_gets_a_gate_that_works() -> None:
    """Documents are light; the strict lane needs the labels check; Dependabot is trusted."""
    gate = parse_config(BASE).gate
    assert gate == Gate() == Config.default("0.1").gate
    assert gate.context == "Merge gate"
    assert gate.light == (
        "docs/**/*.md",
        "docs/**/*.svg",
        "docs/**/*.png",
        "docs/**/*.jpg",
        "docs/**/*.jpeg",
        "docs/**/*.gif",
        "docs/**/*.webp",
        "changes/*.md",
        "README.md",
        "CONTRIBUTING.md",
        "SUPPORT.md",
        "CODE_OF_CONDUCT.md",
    )
    assert (gate.control, gate.checks) == ((), ())
    assert gate.statuses == gate.light_blocks == ("Review labels",)
    assert (gate.trusted_bots, gate.max_light_files) == (("dependabot[bot]",), 100)


def test_the_strict_lane_needs_the_reviews_the_labels_check_waits_for() -> None:
    """``statuses`` follows ``labels.wait_for``, each named once, unless it is set."""
    waits = BASE + '[labels]\nwait_for = ["Alpha review", "Beta review", "Review labels"]\n'
    assert parse_config(waits).gate.statuses == ("Alpha review", "Beta review", "Review labels")
    own = waits + '[gate]\nstatuses = ["Gamma review"]\n'
    assert parse_config(own).gate.statuses == ("Gamma review",)


def test_a_changelog_fragment_is_light_in_the_folder_the_project_uses() -> None:
    """``light`` follows ``changelog.dir`` unless it is set."""
    moved = parse_config(BASE + '[changelog]\ndir = "news"\n').gate
    assert moved.light == gate_light("news") and "news/*.md" in moved.light
    own = parse_config(BASE + '[changelog]\ndir = "news"\n[gate]\nlight = ["guide/**"]\n').gate
    assert own.light == ("guide/**",)


def test_every_key_can_be_set() -> None:
    """The whole table, read back."""
    text = BASE + (
        "[gate]\n"
        'context = "May merge"\n'
        "light = []\n"
        'control = ["tools/**", "LICENSE"]\n'
        "statuses = []\n"
        'checks = [{ name = "Outside Review", app = 4242 }]\n'
        "light_blocks = []\n"
        'trusted_bots = ["dependabot[bot]", "helper[bot]"]\n'
        "max_light_files = 20\n"
    )
    assert parse_config(text).gate == Gate(
        context="May merge",
        light=(),
        control=("tools/**", "LICENSE"),
        statuses=(),
        checks=(GateCheck("Outside Review", 4242),),
        light_blocks=(),
        trusted_bots=("dependabot[bot]", "helper[bot]"),
        max_light_files=20,
    )


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("bogus = 1", r"unknown key gate\.bogus"),
        ('context = ""', r"gate\.context"),
        ("context = 7", r"gate\.context"),
        ('light = "docs/**"', r"gate\.light"),
        ('light = ["/docs/*.md"]', r"gate\.light.*inside"),
        ('light = ["docs/../**"]', r"gate\.light.*inside"),
        ('control = ["a\\\\b"]', r"gate\.control.*inside"),
        ("control = [1]", r"gate\.control"),
        ('statuses = "Review labels"', r"gate\.statuses"),
        ('checks = "x"', r"gate\.checks"),
        ('checks = [{ name = "A" }]', r"gate\.checks\[0\]"),
        ('checks = [{ name = "A", app = 1, more = 2 }]', r"gate\.checks\[0\]"),
        ('checks = [{ name = "", app = 1 }]', r"gate\.checks\[0\]\.name"),
        ('checks = [{ name = "A", app = "1" }]', r"gate\.checks\[0\]\.app"),
        ('checks = [{ name = "A", app = true }]', r"gate\.checks\[0\]\.app"),
        ('checks = [{ name = "A", app = 0 }]', r"gate\.checks\[0\]\.app"),
        ("light_blocks = [true]", r"gate\.light_blocks"),
        ('trusted_bots = "dependabot[bot]"', r"gate\.trusted_bots"),
        ("max_light_files = 0", r"gate\.max_light_files"),
        ("max_light_files = true", r"gate\.max_light_files"),
        # A strict lane that needs nothing would pass everything.
        ("statuses = []", r"both empty"),
        # One name, one signal.
        ('statuses = ["A", "A"]', r"twice"),
        ('statuses = ["A"]\nchecks = [{ name = "A", app = 1 }]', r"twice"),
        # The gate would wait for itself for ever.
        ('statuses = ["Merge gate"]', r"its own status"),
        ('light_blocks = ["Merge gate"]', r"its own status"),
        ('context = "Review labels"', r"its own status"),
    ],
)
def test_an_invalid_gate_is_refused_with_its_key(body: str, message: str) -> None:
    """A typo or a wrong type is an error, never a silent default."""
    with pytest.raises(ConfigError, match=message):
        parse_config(f"{BASE}[gate]\n{body}\n")


@pytest.mark.parametrize(
    "context",
    [
        "Review labels",
        "review labels",
        "Council review",
        "Gemini review",
        "Reflection review",
        "Anthropic review",
        "Helper review",
        "Outside checks",
    ],
)
def test_the_gate_cannot_take_the_name_of_a_status_another_job_posts(context: str) -> None:
    """That job would write its own success under the gate's name."""
    text = BASE + '[labels]\nwait_for = ["Outside checks"]\n'
    text += '[review.reviewers.helper]\nkind = "openai"\n'
    # The gate waits for none of them here, so only the name itself is the reason.
    text += '[gate]\nstatuses = ["Some status"]\nlight_blocks = []\n'
    with pytest.raises(ConfigError, match="a status another devloop job posts"):
        parse_config(f'{text}context = "{context}"\n')
    assert parse_config(f'{text}context = "May merge"\n').gate.context == "May merge"


def test_the_gate_must_be_a_table() -> None:
    """``gate = 1`` is not a section."""
    with pytest.raises(ConfigError, match=r"\[gate\] must be a table"):
        parse_config('gate = 1\n[framework]\nversion = "0.1"\n')

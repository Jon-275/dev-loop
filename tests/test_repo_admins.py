"""Tests that administrators count as held only when every source of an expected rule holds them.

States: each source (classic branch protection, each ruleset on the branch) supplies some
protections and either holds administrators or lets them bypass. Invariant: the answer is
yes only when every source that supplies a protection the project expects holds them;
a source that supplies nothing expected does not count; an unreadable source is no answer.
"""

from __future__ import annotations

from typing import Any

import pytest
from repo_helpers import (
    NOT_PROTECTED,
    PROTECTION,
    RULESETS,
    Fake,
    R,
    baseline,
    on,
    pull_request,
    refused,
    ruleset,
    with_rulesets,
)

from devloop import repo_settings as rs
from devloop.config import GitHub, parse_config
from devloop.repo_admins import admins
from devloop.repo_read import Reader, Seen, read

CHECKS = {
    "type": "required_status_checks",
    "parameters": {"required_status_checks": [{"context": "check", "integration_id": 15368}]},
}
BYPASS = [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}]
EVERYTHING = (
    CHECKS,
    {"type": "deletion"},
    {"type": "non_fast_forward"},
    pull_request(required_review_thread_resolution=True),
)


def _admins(answers: dict[str, Any], config: str = "") -> tuple[Seen, str]:
    """Return the answer for ``answers``, with ``config`` as the lines of ``[github]``."""
    github = parse_config(f'[framework]\nversion = "0.11"\n[github]\n{config}').github
    facts = read(Reader("t0ken", Fake(answers), lambda _: None), "acme/widgets")
    return admins(github, facts)


def _line(answers: dict[str, Any]) -> rs.Line:
    """Return the report's ``enforce_admins`` line for ``answers``."""
    facts = read(Reader("t0ken", Fake(answers), lambda _: None), "acme/widgets")
    return next(line for line in rs.check(GitHub(), facts) if line.setting == "enforce_admins")


def test_a_ruleset_that_supplies_the_checks_and_lets_administrators_bypass_differs() -> None:
    """Classic binds administrators but requires no checks; the ruleset that does, does not."""
    answers = with_rulesets(baseline(), ruleset(7, CHECKS, bypass_actors=BYPASS))
    answers[PROTECTION]["required_status_checks"] = None
    assert _admins(answers) == (Seen(False), "administrators may bypass ruleset 'rules-7'")
    line = _line(answers)
    assert line.status == rs.DIFFERS
    assert line.text() == (
        "differs    enforce_admins: expected true, found false "
        "(administrators may bypass ruleset 'rules-7')"
    )


def test_both_sources_supplying_and_enforcing_is_ok() -> None:
    answers = with_rulesets(baseline(), ruleset(7, *EVERYTHING))
    assert _admins(answers) == (Seen(True), "")
    assert _line(answers).text() == "ok         enforce_admins: true"


@pytest.mark.parametrize("classic", [NOT_PROTECTED, None])
def test_one_source_that_supplies_everything_and_enforces_is_ok(classic: object) -> None:
    alone = with_rulesets(baseline(), ruleset(7, *EVERYTHING))
    if classic is None:  # protected, and nothing a project expects is switched on
        empty = {"required_conversation_resolution": on(False), "enforce_admins": on(False)}
        alone[PROTECTION] = {**empty, "allow_force_pushes": on(True), "allow_deletions": on(True)}
    else:
        alone[PROTECTION] = classic
    assert _admins(alone) == (Seen(True), "")
    assert _admins(baseline()) == (Seen(True), "")  # classic alone


def test_a_bypassing_source_that_supplies_nothing_expected_does_not_count() -> None:
    tags = {"type": "required_linear_history"}
    answers = with_rulesets(baseline(), ruleset(7, tags, bypass_actors=BYPASS))
    assert _admins(answers) == (Seen(True), "")
    owner = with_rulesets(
        baseline(),
        ruleset(7, bypass_actors=BYPASS),
        ruleset(8, pull_request(require_code_owner_review=True), bypass_actors=BYPASS),
    )
    skip = 'require_pull_request = "skip"\n'
    assert _admins(owner, skip) == (Seen(True), "")  # code-owner review is advice by default
    said = _admins(owner, skip + "code_owner_review = true\n")
    assert said == (Seen(False), "administrators may bypass ruleset 'rules-8'")


def test_classic_protection_that_does_not_enforce_is_named() -> None:
    answers = with_rulesets(baseline(), ruleset(7, {"type": "deletion"}))
    answers[PROTECTION]["enforce_admins"] = on(False)
    assert _admins(answers) == (Seen(False), "administrators may bypass classic branch protection")
    answers[f"{R}/rulesets/7"]["bypass_actors"] = BYPASS
    note = "administrators may bypass classic branch protection, ruleset 'rules-7'"
    assert _admins(answers) == (Seen(False), note)


def test_no_source_of_an_expected_rule_is_not_held() -> None:
    answers = {**baseline(), PROTECTION: NOT_PROTECTED}
    assert _admins(answers) == (Seen(False), "no source supplies a rule the project expects")


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({PROTECTION: refused(403, "Forbidden")}, "branch protection: HTTP 403 'Forbidden'"),
        ({RULESETS: refused(403, "Forbidden")}, "rulesets: HTTP 403 'Forbidden'"),
        ({f"{R}/rulesets/7": refused(404)}, "ruleset 7: HTTP 404"),
        ({f"{R}/rulesets/7": {**ruleset(7, CHECKS), "bypass_actors": None}}, "bypass list"),
    ],
)
def test_a_source_that_cannot_be_read_is_not_judged(change: dict[str, Any], reason: str) -> None:
    answers = {**with_rulesets(baseline(), ruleset(7, *EVERYTHING)), **change}
    seen, note = _admins(answers)
    assert seen.value is None and reason in seen.reason and note == ""
    assert _line(answers).status == rs.NOT_JUDGED

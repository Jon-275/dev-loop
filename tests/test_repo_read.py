"""Tests for the read-only reader of a repository's GitHub settings."""

from __future__ import annotations

from typing import Any

import pytest
from repo_helpers import (
    ACTIONS,
    FORK,
    NOT_PROTECTED,
    PROTECTION,
    REPORTING,
    RULESETS,
    WORKFLOW,
    Fake,
    R,
    baseline,
    on,
    pull_request,
    refused,
    ruleset,
    with_rulesets,
)

from devloop import repo_read as rr
from devloop.repo_read import Facts, Read, Reader, Seen, read
from devloop.review.github import Reply
from devloop.review.model import ReviewError


def _read(answers: dict[str, Any]) -> tuple[Facts, Fake]:
    """Read the repository ``answers`` describes; return the facts and the client."""
    fake = Fake(answers)
    return read(Reader("t0ken", fake, lambda _: None), "acme/widgets"), fake


def test_every_setting_of_a_repository_is_read() -> None:
    answers = baseline()
    facts, fake = _read(answers)
    assert facts.repo == Read(answers[R])
    assert facts.protection == Read(answers[PROTECTION])
    assert (facts.rulesets, facts.rulesets_reason) == ((), "")
    assert facts.actions.data == answers[ACTIONS]
    assert facts.workflow.data == answers[WORKFLOW]
    assert facts.fork_approval.data == answers[FORK]
    assert facts.reporting.data == answers[REPORTING]
    assert len(fake.calls) == 7


def test_only_get_is_ever_asked_of_the_client() -> None:
    rules = with_rulesets(baseline(), ruleset(7, {"type": "deletion"}), ruleset(8))
    for answers in (rules, {}, {**baseline(), PROTECTION: refused(403, "Forbidden")}):
        _, fake = _read(answers)
        assert fake.calls
        assert {(method, body) for method, _, body in fake.calls} == {("GET", None)}


@pytest.mark.parametrize(
    ("answer", "reason", "status"),
    [
        (
            refused(403, "Resource not  accessible\nby integration"),
            "HTTP 403 'Resource not accessible by integration': the token may not read this; "
            "it needs admin rights on the repository",
            403,
        ),
        (
            refused(404),
            "HTTP 404 'Not Found': GitHub does not offer this for the repository, "
            "or the token may not see it",
            404,
        ),
        (
            refused(422, "x" * 500),
            f"HTTP 422 '{'x' * 120}': GitHub does not offer this for this kind of repository",
            422,
        ),
        (Reply(401, None), "HTTP 401", 401),
        (Reply(503, {"message": 5}), "HTTP 503", 503),
        (
            ReviewError("GitHub's reply (200) is not JSON"),
            "GitHub could not be read: GitHub's reply (200) is not JSON",
            0,
        ),
        (Reply(200, ["a list"]), "GitHub's answer is not a record", 200),
        (Reply(200, None), "GitHub's answer is not a record", 200),
    ],
)
def test_a_refused_or_malformed_answer_keeps_its_reason(
    answer: Reply | Exception, reason: str, status: int
) -> None:
    for path in (PROTECTION, ACTIONS, WORKFLOW, FORK, REPORTING):
        facts, _ = _read({**baseline(), path: answer})
        reads = (facts.protection, facts.actions, facts.workflow, facts.fork_approval)
        failed = [one for one in (*reads, facts.reporting) if one.reason]
        assert failed == [Read(None, reason, status, failed[0].message)]


def test_a_read_is_retried_as_every_devloop_call_is() -> None:
    replies = [Reply(502, None), Reply(200, {"enabled": True})]
    waits: list[float] = []
    reader = Reader("t0ken", lambda *_: replies.pop(0), waits.append)
    assert reader.get("x") == Read({"enabled": True})
    assert waits == [15.0]


def test_a_branch_without_protection_is_an_answer_not_a_failed_read() -> None:
    facts, _ = _read({**baseline(), PROTECTION: NOT_PROTECTED})
    assert facts.protection == Read(absent=True)
    other, _ = _read({**baseline(), PROTECTION: refused(404, "Branch not found")})
    assert not other.protection.absent and other.protection.reason.startswith("HTTP 404")


def test_an_unknown_default_branch_leaves_the_branch_rules_unread() -> None:
    facts, fake = _read({})
    why = "the default branch is not known: HTTP 404 'Not Found'"
    assert facts.protection.reason.startswith(why) and facts.rulesets_reason.startswith(why)
    assert not any("/branches/" in url or "/rulesets" in url for _, url, _ in fake.calls)
    unnamed, _ = _read({**baseline(), R: {"full_name": "acme/widgets"}})
    assert unnamed.protection.reason == "the default branch is not known: GitHub did not name it"


def test_the_default_branch_is_quoted_in_the_address() -> None:
    answers = baseline()
    answers[R]["default_branch"] = "release/1.x"
    answers[f"{R}/branches/release%2F1.x/protection"] = answers.pop(PROTECTION)
    assert _read(answers)[0].protection.reason == ""


def test_the_rulesets_that_cover_the_default_branch_are_read_whole() -> None:
    patterns = {"ref_name": {"include": ["refs/heads/ma*"], "exclude": []}}
    covering = [ruleset(7, pull_request(require_code_owner_review=True)), ruleset(8)]
    covering.append(ruleset(9, conditions=patterns))
    others = [
        ruleset(10, enforcement="disabled"),
        ruleset(11, enforcement="evaluate"),
        ruleset(12, target="tag"),
        ruleset(13, conditions={"ref_name": {"include": ["refs/heads/release/*"], "exclude": []}}),
        ruleset(14, conditions={"ref_name": {"include": ["~ALL"], "exclude": ["refs/heads/main"]}}),
    ]
    facts, _ = _read(with_rulesets(baseline(), *covering, *others))
    assert [found["id"] for found in facts.rulesets] == [7, 8, 9]
    assert facts.rulesets[0]["rules"] == covering[0]["rules"]
    assert facts.rulesets_reason == ""


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({RULESETS: refused(403, "Forbidden")}, "rulesets: HTTP 403 'Forbidden'"),
        ({RULESETS: {"not": "a list"}}, "rulesets: GitHub's answer is not a list"),
        ({RULESETS: [{"id": n} for n in range(100)]}, "fewer than 100 rulesets"),
        ({RULESETS: [{"name": "no id"}]}, "rulesets: GitHub's list names a ruleset with no id"),
        ({f"{R}/rulesets/8": refused(404)}, "ruleset 8: HTTP 404"),
        ({f"{R}/rulesets/8": {"id": 8}}, "ruleset 8: GitHub's answer has no rules"),
        ({f"{R}/rulesets/8": ["a list"]}, "ruleset 8: GitHub's answer has no rules"),
    ],
)
def test_rulesets_that_cannot_all_be_read_are_not_taken_for_none(
    change: dict[str, Any], reason: str
) -> None:
    answers = with_rulesets(baseline(), ruleset(7, {"type": "deletion"}), ruleset(8))
    facts, _ = _read({**answers, **change})
    assert facts.rulesets == ()
    assert reason in facts.rulesets_reason


def _covered(include: list[str], exclude: list[str], branch: str = "team/api/v2") -> bool:
    """Return whether a ruleset with these patterns is read as covering ``branch``."""
    answers = baseline()
    answers[R]["default_branch"] = branch
    answers[f"{R}/branches/{branch.replace('/', '%2F')}/protection"] = answers.pop(PROTECTION)
    rules = ruleset(7, conditions={"ref_name": {"include": include, "exclude": exclude}})
    facts, _ = _read(with_rulesets(answers, rules))
    assert facts.rulesets_reason == ""
    return bool(facts.rulesets)


@pytest.mark.parametrize(
    ("pattern", "covers"),
    [
        ("refs/heads/team/api/v2", True),
        ("refs/heads/*", False),  # one star stays inside one part of the name
        ("refs/heads/team/*", False),
        ("refs/heads/team/*/*", True),
        ("refs/heads/team/*/v?", True),
        ("refs/heads/**", True),
        ("refs/heads/**/*", True),
        ("refs/heads/**/v2", True),
        ("refs/heads/team/**", True),
        ("refs/heads/other/**", False),
        ("refs/heads/team/api", False),
        ("refs/tags/**", False),
        ("~DEFAULT_BRANCH", True),
        ("~ALL", True),
    ],
)
def test_a_ruleset_pattern_matches_a_branch_as_github_matches_it(
    pattern: str, covers: bool
) -> None:
    assert _covered([pattern], []) is covers
    assert _covered(["~ALL"], [pattern]) is not covers
    assert _covered(["refs/heads/nothing", pattern], ["refs/heads/nothing"]) is covers


def test_one_star_covers_a_branch_with_a_one_part_name() -> None:
    assert _covered(["refs/heads/*"], [], "main") and _covered(["refs/heads/ma*"], [], "main")


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"target": None}, "ruleset 7: GitHub's answer has no target or no enforcement"),
        ({"enforcement": None}, "ruleset 7: GitHub's answer has no target or no enforcement"),
        ({"enforcement": 1}, "ruleset 7: GitHub's answer has no target or no enforcement"),
        ({"conditions": None}, "ruleset 7: GitHub's answer has no conditions.ref_name.include"),
        ({"conditions": {}}, "ruleset 7: GitHub's answer has no conditions.ref_name.include"),
        (
            {"conditions": {"ref_name": {"exclude": []}}},
            "ruleset 7: GitHub's answer has no conditions.ref_name.include",
        ),
        (
            {"conditions": {"ref_name": {"include": ["~ALL"]}}},
            "ruleset 7: GitHub's answer has no conditions.ref_name.exclude",
        ),
        (
            {"conditions": {"ref_name": {"include": "~ALL", "exclude": []}}},
            "ruleset 7: GitHub's answer has no conditions.ref_name.include",
        ),
        (
            {"conditions": {"ref_name": {"include": ["~ALL"], "exclude": [7]}}},
            "ruleset 7: GitHub's answer has no conditions.ref_name.exclude",
        ),
    ],
)
def test_a_ruleset_that_cannot_be_placed_is_not_taken_for_one_that_does_not_apply(
    change: dict[str, Any], reason: str
) -> None:
    made = ruleset(7, {"type": "deletion"}, **change)
    for key, new in change.items():
        if new is None:
            del made[key]
    facts, _ = _read(with_rulesets(baseline(), made, ruleset(8)))
    assert (facts.rulesets, facts.rulesets_reason) == ((), reason)
    assert rr.rule(facts, "deletion") == Seen(reason=reason)


def test_a_ruleset_for_tags_or_one_not_enforced_needs_no_conditions() -> None:
    quiet = [ruleset(7, target="tag", conditions={}), ruleset(8, enforcement="disabled")]
    del quiet[1]["conditions"]
    facts, _ = _read(with_rulesets(baseline(), *quiet))
    assert (facts.rulesets, facts.rulesets_reason) == ((), "")


def test_a_rule_is_read_from_classic_protection_and_from_the_rulesets() -> None:
    made = ruleset(7, {"type": "deletion"}, pull_request(require_code_owner_review=True))
    facts, _ = _read(with_rulesets(baseline(), made))
    assert rr.classic(facts, "allow_deletions", "enabled", absent=True) == Seen(False)
    assert rr.classic(facts, "no_such", "enabled", absent=True).reason.endswith("no_such.enabled")
    assert rr.classic(facts, "no_such", "enabled", absent=True, missing=False) == Seen(False)
    assert rr.rule(facts, "deletion") == Seen(True)
    assert rr.rule(facts, "non_fast_forward") == Seen(False)
    assert rr.rule(facts, "pull_request", "require_code_owner_review") == Seen(True)
    assert rr.rule(facts, "pull_request", "required_review_thread_resolution") == Seen(False)
    assert rr.rules(facts, "deletion") == [{}]
    assert rr.value(facts.reporting, bool, "enabled") == Seen(True)
    assert "no enabled (hint)" in rr.value(facts.workflow, bool, "enabled", hint=" (hint)").reason
    bare, _ = _read({**baseline(), PROTECTION: NOT_PROTECTED})
    assert rr.classic(bare, "allow_deletions", "enabled", absent=True) == Seen(True)
    refused_, _ = _read({**baseline(), PROTECTION: refused(403, "No"), ACTIONS: on(True)})
    why = rr.classic(refused_, "allow_deletions", "enabled", absent=True).reason
    assert why.startswith("branch protection: HTTP 403")


def test_a_rule_is_there_when_either_source_makes_it_and_absent_only_when_both_say_so() -> None:
    yes, no, unknown = Seen(True), Seen(False), Seen(reason="HTTP 403")
    assert rr.either(no, yes) == rr.either(unknown, yes) == yes
    assert rr.either(no, no) == no
    assert rr.either(no, unknown) == unknown
    assert rr.either(unknown, Seen(reason="x")) == Seen(reason="HTTP 403; x")
    assert rr.opposite(yes) == no and rr.opposite(unknown) == unknown


@pytest.mark.parametrize(
    "entry",
    [
        None,
        "deletion",
        ["deletion"],
        {"parameters": {}},
        {"type": 7},
        {"type": "pull_request", "parameters": None},
        {"type": "pull_request", "parameters": ["require_code_owner_review"]},
    ],
)
def test_a_rule_that_is_not_a_rule_leaves_the_rulesets_unread(entry: object) -> None:
    made = ruleset(7, {"type": "deletion"})
    made["rules"].append(entry)
    facts, _ = _read(with_rulesets(baseline(), made))
    reason = "ruleset 7: GitHub's answer has a rule that is not a rule"
    assert (facts.rulesets, facts.rulesets_reason) == ((), reason)
    assert rr.rule(facts, "deletion") == Seen(reason=reason)
    assert rr.rule(facts, "pull_request", "require_code_owner_review") == Seen(reason=reason)
    elsewhere, _ = _read(with_rulesets(baseline(), {**made, "target": "tag"}))
    assert elsewhere.rulesets_reason == ""  # a ruleset that is not for branches is not read


@pytest.mark.parametrize("parameters", [{}, {"require_code_owner_review": None}, None])
def test_a_rule_that_does_not_say_the_parameter_asked_for_is_a_reason(
    parameters: dict[str, Any] | None,
) -> None:
    asked: dict[str, Any] = {"type": "pull_request"}
    if parameters is not None:
        asked["parameters"] = parameters
    full = pull_request(require_code_owner_review=True)
    facts, _ = _read(with_rulesets(baseline(), ruleset(7, full), ruleset(8, asked)))
    assert facts.rulesets_reason == ""
    assert rr.rule(facts, "pull_request") == Seen(True)
    assert rr.rule(facts, "pull_request", "require_code_owner_review") == Seen(
        reason="rulesets: a pull_request rule does not say require_code_owner_review"
    )


def test_a_rule_without_parameters_is_read() -> None:
    facts, _ = _read(with_rulesets(baseline(), ruleset(7, {"type": "non_fast_forward"})))
    assert rr.rule(facts, "non_fast_forward") == Seen(True)
    assert rr.rules(facts, "non_fast_forward") == [{}]
    assert rr.rule(facts, "pull_request", "require_code_owner_review") == Seen(False)

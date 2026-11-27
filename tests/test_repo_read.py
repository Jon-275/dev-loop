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
    pull_request,
    refused,
    ruleset,
    with_rulesets,
)

from devloop.repo_read import Facts, Read, Reader, read
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
        ruleset(15, conditions={}),
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

"""Tests for ``devloop doctor --repo``: live GitHub settings against ``[github]``."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
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

from devloop import repo_settings as rs
from devloop.cli import main as cli
from devloop.config import GitHub, parse_config
from devloop.repo_read import Reader, read
from devloop.review.github import Reply
from devloop.review.model import ReviewError

CONFIG = '[framework]\nversion = "0.11"\n[github]\nrequired_checks = ["check", "Merge gate"]\n'


def _lines(answers: dict[str, Any], config: str = CONFIG) -> dict[str, rs.Line]:
    """Return the report for ``answers``, by setting."""
    reader = Reader("t0ken", Fake(answers), lambda _: None)
    lines = rs.check(parse_config(config).github, read(reader, "acme/widgets"))
    assert len({line.setting for line in lines}) == len(lines)
    return {line.setting: line for line in lines}


def _run(tmp_path: Path, answers: dict[str, Any], *, as_json: bool = False) -> tuple[int, Fake]:
    """Run the command against ``answers`` and return its exit code and the client."""
    (tmp_path / "devloop.toml").write_text(CONFIG, encoding="utf-8")
    fake = Fake(answers)
    env = {"GITHUB_TOKEN": "t0ken"}
    return rs.main(tmp_path, "acme/widgets", as_json, env, fake, lambda _: None), fake


def test_a_repository_at_the_baseline_is_ok_on_every_setting(tmp_path: Path) -> None:
    lines = _lines(baseline())
    assert set(lines) == {field.name for field in dataclasses.fields(GitHub)}
    assert {line.status for line in lines.values()} == {rs.OK}
    assert lines["code_owner_review"].expected == "advice"
    assert _run(tmp_path, baseline())[0] == 0


def test_only_get_is_ever_asked_of_the_client(tmp_path: Path) -> None:
    answers = with_rulesets(baseline(), ruleset(7, {"type": "deletion"}))
    for case in (answers, {}, {**baseline(), PROTECTION: refused(403, "Forbidden")}):
        _, fake = _run(tmp_path, case)
        assert fake.calls
        assert {(method, body) for method, _, body in fake.calls} == {("GET", None)}


DIFFERENT: dict[str, tuple[str, Any, object]] = {
    "required_checks": (PROTECTION, {"required_status_checks": None}, []),
    "enforce_admins": (PROTECTION, {"enforce_admins": on(False)}, False),
    "allow_force_pushes": (PROTECTION, {"allow_force_pushes": on(True)}, True),
    "allow_deletions": (PROTECTION, {"allow_deletions": on(True)}, True),
    "conversation_resolution": (
        PROTECTION,
        {"required_conversation_resolution": on(False)},
        False,
    ),
    "code_owner_review": (
        PROTECTION,
        {"required_pull_request_reviews": {"require_code_owner_reviews": True}},
        True,
    ),
    "default_token_read_only": (WORKFLOW, {"default_workflow_permissions": "write"}, False),
    "sha_pinning_required": (ACTIONS, {"sha_pinning_required": False}, False),
    "fork_pr_approval": (
        FORK,
        {"approval_policy": "first_time_contributors"},
        "first_time_contributors",
    ),
    "secret_scanning": (
        R,
        {"security_and_analysis": {"secret_scanning": {"status": "disabled"}}},
        False,
    ),
    "private_vulnerability_reporting": (REPORTING, on(False), False),
    "delete_branch_on_merge": (R, {"delete_branch_on_merge": False}, False),
    "merge_methods": (R, {"allow_squash_merge": True}, ["merge", "squash"]),
}


@pytest.mark.parametrize("setting", DIFFERENT)
def test_a_setting_that_differs_says_what_was_expected_and_found(setting: str) -> None:
    path, change, found = DIFFERENT[setting]
    answers = baseline()
    answers[path].update(change)
    line = _lines(answers)[setting]
    assert (line.status, line.found) == (rs.DIFFERS, found)
    assert f"expected {json.dumps(line.expected)}, found {json.dumps(found)}" in line.text()
    assert rs.exit_code([line]) == 1


def test_push_protection_and_unbound_checks_differ() -> None:
    answers = baseline()
    answers[R]["security_and_analysis"]["secret_scanning_push_protection"]["status"] = "disabled"
    answers[PROTECTION]["required_status_checks"]["checks"][0]["app_id"] = None
    lines = _lines(answers)
    assert lines["push_protection"].status == rs.DIFFERS
    assert lines["required_checks"].status == rs.OK
    assert lines["required_checks_bound"].status == rs.DIFFERS
    assert "any source may post: check" in lines["required_checks_bound"].text()
    relaxed = _lines(answers, CONFIG + "required_checks_bound = false\n")
    assert relaxed["required_checks_bound"].status == rs.OK


def test_no_required_check_at_all_is_not_bound_checks() -> None:
    answers = {**baseline(), PROTECTION: NOT_PROTECTED}
    line = _lines(answers)["required_checks_bound"]
    assert (line.status, line.reason) == (rs.DIFFERS, "no status check is required")


def test_an_unprotected_branch_is_judged_not_unreadable() -> None:
    lines = _lines({**baseline(), PROTECTION: NOT_PROTECTED})
    found = {key: lines[key].found for key in ("enforce_admins", "allow_force_pushes")}
    assert found == {"enforce_admins": False, "allow_force_pushes": True}
    assert lines["allow_deletions"].status == rs.DIFFERS
    assert lines["conversation_resolution"].status == rs.DIFFERS
    assert (lines["required_checks"].status, lines["required_checks"].found) == (rs.DIFFERS, [])
    assert lines["code_owner_review"].status == rs.OK


UNREADABLE = {
    PROTECTION: ("enforce_admins", "allow_force_pushes", "allow_deletions", "required_checks")
    + ("required_checks_bound", "conversation_resolution", "code_owner_review"),
    # The rules classic protection already makes stay judged: one source is enough.
    RULESETS: ("merge_methods", "required_checks", "required_checks_bound", "code_owner_review"),
    ACTIONS: ("sha_pinning_required",),
    WORKFLOW: ("default_token_read_only",),
    FORK: ("fork_pr_approval",),
    REPORTING: ("private_vulnerability_reporting",),
}
REFUSALS = {
    "403": (refused(403, "Resource not accessible by integration"), "HTTP 403"),
    "404": (refused(404), "HTTP 404 'Not Found': GitHub does not offer this"),
    "422": (refused(422, "Not available"), "HTTP 422 'Not available': GitHub does not offer"),
    "not JSON": (ReviewError("GitHub's reply (200) is not JSON"), "is not JSON"),
    "another shape": (Reply(200, ["unexpected"]), ""),
    "5xx": (Reply(503, None), "HTTP 503"),
}


@pytest.mark.parametrize("path", UNREADABLE)
@pytest.mark.parametrize("refusal", REFUSALS)
def test_what_cannot_be_read_is_not_judged_with_the_reason(path: str, refusal: str) -> None:
    answer, reason = REFUSALS[refusal]
    lines = _lines({**baseline(), path: answer})
    for setting in UNREADABLE[path]:
        line = lines[setting]
        assert line.status == rs.NOT_JUDGED, setting
        assert reason in line.reason and line.reason in line.text()
    others = set(lines) - set(UNREADABLE[path])
    assert {lines[setting].status for setting in others} == {rs.OK}
    assert rs.exit_code(list(lines.values())) == 3


def test_an_unreadable_repository_judges_nothing(tmp_path: Path) -> None:
    lines = _lines({})
    assert {line.status for line in lines.values()} == {rs.NOT_JUDGED}
    assert "the default branch is not known: HTTP 404" in lines["enforce_admins"].reason
    assert _run(tmp_path, {})[0] == 3
    unnamed = _lines({**baseline(), R: {"full_name": "acme/widgets"}})
    assert "GitHub did not name it" in unnamed["allow_deletions"].reason


def test_fields_a_token_without_rights_is_not_shown_are_not_judged() -> None:
    answers = baseline()
    for key in ("security_and_analysis", "allow_merge_commit", "delete_branch_on_merge"):
        del answers[R][key]
    lines = _lines(answers)
    assert "needs admin rights" in lines["secret_scanning"].reason
    assert lines["push_protection"].status == rs.NOT_JUDGED
    assert "needs write access" in lines["merge_methods"].reason
    assert lines["delete_branch_on_merge"].status == rs.NOT_JUDGED


def test_a_ruleset_can_make_the_rule_that_classic_protection_does_not() -> None:
    rules = ruleset(
        7,
        {"type": "deletion"},
        {"type": "non_fast_forward"},
        pull_request(required_review_thread_resolution=True, allowed_merge_methods=["merge"]),
        {
            "type": "required_status_checks",
            "parameters": {
                "required_status_checks": [
                    {"context": "check", "integration_id": 15368},
                    {"context": "Merge gate", "integration_id": 15368},
                ]
            },
        },
    )
    answers = with_rulesets({**baseline(), PROTECTION: NOT_PROTECTED}, rules)
    answers[R].update(allow_squash_merge=True)
    lines = _lines(answers)
    assert {line.status for line in lines.values()} == {rs.OK}
    answers[f"{R}/rulesets/7"]["bypass_actors"] = [{"actor_type": "RepositoryRole"}]
    assert _lines(answers)["enforce_admins"].status == rs.DIFFERS
    del answers[f"{R}/rulesets/7"]["bypass_actors"]
    assert _lines(answers)["enforce_admins"].status == rs.NOT_JUDGED


def test_a_ruleset_rule_counts_when_classic_protection_cannot_be_read() -> None:
    rules = ruleset(7, {"type": "deletion"})
    answers = with_rulesets({**baseline(), PROTECTION: refused(403, "Forbidden")}, rules)
    lines = _lines(answers)
    assert lines["allow_deletions"].status == rs.OK
    assert lines["allow_force_pushes"].status == rs.NOT_JUDGED


@pytest.mark.parametrize(
    "changes",
    [
        {"enforcement": "disabled"},
        {"enforcement": "evaluate"},
        {"target": "tag"},
        {"conditions": {"ref_name": {"include": ["refs/heads/release/*"], "exclude": []}}},
        {"conditions": {"ref_name": {"include": ["~ALL"], "exclude": ["refs/heads/main"]}}},
    ],
)
def test_a_ruleset_that_does_not_cover_the_default_branch_makes_no_rule(
    changes: dict[str, Any],
) -> None:
    rules = ruleset(7, pull_request(require_code_owner_review=True), **changes)
    lines = _lines(with_rulesets(baseline(), rules))
    assert lines["code_owner_review"].found is False
    assert "code_owner_review_sources" not in lines


def test_an_unreadable_ruleset_leaves_its_rules_not_judged() -> None:
    answers = with_rulesets(baseline(), ruleset(7))
    answers[f"{R}/rulesets/7"] = refused(403, "Forbidden")
    line = _lines(answers)["conversation_resolution"]
    assert line.status == rs.OK  # classic protection requires it: one source is enough
    assert "ruleset 7: HTTP 403" in _lines(answers)["code_owner_review"].reason


def test_sources_that_disagree_on_code_owner_review_get_their_own_line() -> None:
    rules = ruleset(7, pull_request(require_code_owner_review=True))
    lines = _lines(with_rulesets(baseline(), rules))
    assert (lines["code_owner_review"].status, lines["code_owner_review"].found) == (
        rs.DIFFERS,
        True,
    )
    line = lines["code_owner_review_sources"]
    assert line.status == rs.DIFFERS
    assert "classic branch protection says false and a ruleset says true" in line.text()
    agreed = _lines(with_rulesets(baseline(), rules), CONFIG + "code_owner_review = true\n")
    assert agreed["code_owner_review"].status == rs.OK
    assert agreed["code_owner_review_sources"].status == rs.DIFFERS
    same = ruleset(7, pull_request(require_code_owner_review=False))
    assert "code_owner_review_sources" not in _lines(with_rulesets(baseline(), same))


def test_skip_names_the_setting_and_judges_nothing() -> None:
    skipped = CONFIG + 'sha_pinning_required = "skip"\ncode_owner_review = "skip"\n'
    answers = {**baseline(), ACTIONS: refused(403, "Forbidden")}
    lines = _lines(answers, skipped.replace('["check", "Merge gate"]', '"skip"'))
    for setting in ("sha_pinning_required", "code_owner_review", "required_checks"):
        assert lines[setting].status == rs.SKIPPED
        assert lines[setting].text().split() == ["skipped", setting]
    assert rs.exit_code(list(lines.values())) == 0


def test_no_expected_checks_is_not_judged_and_shows_what_is_required() -> None:
    line = _lines(baseline(), '[framework]\nversion = "0.11"\n')["required_checks"]
    assert line.status == rs.NOT_JUDGED
    assert line.text().endswith('lists no check to expect; found ["Merge gate", "check"]')


def test_exit_codes_and_the_printed_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    answers = baseline()
    answers[ACTIONS]["sha_pinning_required"] = False
    assert _run(tmp_path, answers)[0] == 1
    out = capsys.readouterr().out.splitlines()
    assert "differs    sha_pinning_required: expected true, found false" in out
    assert 'ok         merge_methods: ["merge"]' in out
    assert out[-1] == "acme/widgets: 14 ok, 1 differ, 0 not judged, 0 skipped"
    answers[FORK] = refused(403, "Forbidden")
    assert _run(tmp_path, answers)[0] == 1  # a difference outranks "not judged"
    assert _run(tmp_path, {**baseline(), FORK: refused(403, "Forbidden")})[0] == 3
    assert "not judged fork_pr_approval: HTTP 403 'Forbidden': the token" in capsys.readouterr().out


def test_json_prints_the_same_lines_as_records(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _ = _run(tmp_path, {**baseline(), REPORTING: refused(404)}, as_json=True)
    records = json.loads(capsys.readouterr().out)
    assert code == 3
    assert len(records) == 15
    assert records[0] == {
        "setting": "required_checks",
        "status": "ok",
        "expected": ["Merge gate", "check"],
        "found": ["Merge gate", "check"],
        "reason": "",
    }
    reporting = next(r for r in records if r["setting"] == "private_vulnerability_reporting")
    assert reporting["status"] == "not judged" and reporting["reason"].startswith("HTTP 404")


def test_the_command_needs_a_token_a_repository_and_a_valid_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = Fake(baseline())
    (tmp_path / "devloop.toml").write_text(CONFIG, encoding="utf-8")
    assert rs.main(tmp_path, "acme/widgets", False, {}, fake) == 2
    assert rs.main(tmp_path, "acme/../widgets", False, {"GITHUB_TOKEN": "t0ken"}, fake) == 2
    (tmp_path / "devloop.toml").write_text(CONFIG + "signed_commits = true\n", encoding="utf-8")
    assert rs.main(tmp_path, "acme/widgets", False, {"GITHUB_TOKEN": "t0ken"}, fake) == 2
    assert "unknown key github.signed_commits" in capsys.readouterr().err
    assert fake.calls == []
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert cli(["doctor", "--root", str(tmp_path), "--json"]) == 2
    assert cli(["doctor", "--root", str(tmp_path), "--repo", "acme/widgets"]) == 2
    assert "t0ken" not in capsys.readouterr().out

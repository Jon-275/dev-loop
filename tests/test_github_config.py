"""Tests for the ``[github]`` table: the repository settings ``devloop doctor --repo`` expects."""

import dataclasses

import pytest

from devloop.config import SKIP, ConfigError, GitHub, parse_config

BASE = '[framework]\nversion = "0.11"\n'


def _github(body: str) -> GitHub:
    """Return the parsed ``[github]`` table whose lines are ``body``."""
    return parse_config(f"{BASE}[github]\n{body}\n").github


def test_the_defaults_are_the_baseline() -> None:
    assert parse_config(BASE).github == GitHub(
        required_checks=(),
        required_checks_bound=True,
        enforce_admins=True,
        allow_force_pushes=False,
        allow_deletions=False,
        conversation_resolution=True,
        code_owner_review="advice",
        default_token_read_only=True,
        sha_pinning_required=True,
        fork_pr_approval="all_external_contributors",
        secret_scanning=True,
        push_protection=True,
        private_vulnerability_reporting=True,
        delete_branch_on_merge=True,
        merge_methods=("merge",),
    )


def test_a_project_lists_only_what_it_does_differently() -> None:
    github = _github(
        'required_checks = ["check", "Merge gate"]\n'
        'merge_methods = ["squash", "merge"]\n'
        "code_owner_review = true\n"
        'fork_pr_approval = "first_time_contributors"\n'
        "sha_pinning_required = false"
    )
    assert github.required_checks == ("check", "Merge gate")
    assert github.merge_methods == ("squash", "merge")
    assert github.code_owner_review is True
    assert github.fork_pr_approval == "first_time_contributors"
    assert github.sha_pinning_required is False
    assert github.enforce_admins is True


@pytest.mark.parametrize("key", [field.name for field in dataclasses.fields(GitHub)])
def test_every_key_can_be_skipped(key: str) -> None:
    assert getattr(_github(f'{key} = "skip"'), key) == SKIP


def test_an_unknown_key_is_refused() -> None:
    with pytest.raises(ConfigError, match=r"unknown key github\.require_signatures"):
        _github("require_signatures = true")


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ('enforce_admins = "yes"', 'github.enforce_admins must be true, false or "skip"'),
        ("enforce_admins = 1", "github.enforce_admins must be true, false"),
        ('code_owner_review = "maybe"', 'true, false, "advice" or "skip"'),
        ('required_checks = "check"', "github.required_checks must be a list"),
        ("required_checks = [1]", "github.required_checks must be a list"),
        ('required_checks = ["a", "a"]', "github.required_checks names a check twice"),
        ('merge_methods = ["fast-forward"]', "github.merge_methods"),
        ("merge_methods = []", "github.merge_methods"),
        ('merge_methods = ["merge", "merge"]', "github.merge_methods"),
        ('fork_pr_approval = "everyone"', "github.fork_pr_approval must be one of"),
        ("fork_pr_approval = true", "github.fork_pr_approval must be one of"),
    ],
)
def test_a_wrong_value_is_refused(line: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        _github(line)


def test_the_table_must_be_a_table() -> None:
    with pytest.raises(ConfigError, match=r"\[github\] must be a table"):
        parse_config(f"github = 1\n{BASE}")

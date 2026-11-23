"""``devloop review``: settings from the trusted checkout, local runs and exit codes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from review_helpers import commit, run

from devloop.cli import main
from devloop.config import parse_config
from devloop.review import command
from devloop.review.model import Effort, Usage
from devloop.review.providers import Ask, Reviewer

TOML = '[framework]\nversion = "0.3"\n'
FINDING = {"severity": "P1", "path": "app.py", "line": 1, "title": "Leak", "body": "Fix."}


def _fake_model(monkeypatch: pytest.MonkeyPatch, asked: list[str]) -> None:
    """Replace the model with one that reports one finding and uses 10 + 2 tokens."""

    def asker(reviewer: Reviewer, provider: object, api_key: str, usage: Usage) -> Ask:
        """Return a model that records its prompts."""

        def ask(prompt: str, effort: Effort) -> str:
            """Record the prompt and answer."""
            asked.append(api_key)
            usage.add(10, 2)
            return json.dumps({"summary": "s", "findings": [FINDING], "not_posted": []})

        return ask

    monkeypatch.setattr(command, "asker", asker)


@pytest.fixture()
def branch(repo: Path) -> Path:
    """Return the repository with ``main`` as base and a branch adding ``app.py``."""
    (repo / "devloop.toml").write_text(TOML)
    commit(repo, "config")
    run(repo, "checkout", "-q", "-b", "work")
    (repo / "app.py").write_text("token = 'x'\nprint(token)\n")
    commit(repo, "add app")
    return repo


def test_a_local_review_prints_the_summary_and_each_finding(
    branch: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    asked: list[str] = []
    _fake_model(monkeypatch, asked)
    env = {"GEMINI_API_KEY": "k"}
    assert command.review("gemini", branch, local=True, base="main", env=env) == 0
    out = capsys.readouterr().out
    assert asked == ["k"]
    assert "### 🤖 Gemini PR Review" in out and "\n--- app.py:1 (RIGHT)\n**P1 — Leak**" in out
    assert "Posted Gemini review with 1 finding thread(s)." in out
    assert "Model usage: 1 request(s), 10 input and 2 output tokens (reasoning included)." in out


def test_a_local_review_without_its_key_fails_the_check_by_name(
    branch: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert command.review("gemini", branch, local=True, base="main", env={}) == 1
    assert "GEMINI_API_KEY is not available" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("toml", "base", "message"),
    [
        (None, "main", "devloop.toml not found"),
        (TOML, "main", "unknown reviewer 'nobody'"),
        (TOML, "no-such-branch", "could not read the branch"),
    ],
)
def test_a_review_that_cannot_run_exits_2(
    branch: Path, capsys: pytest.CaptureFixture[str], toml: str | None, base: str, message: str
) -> None:
    if toml is None:
        (branch / "devloop.toml").unlink()
    name = "nobody" if "nobody" in message else "gemini"
    assert command.review(name, branch, local=True, base=base, env={}) == 2
    assert message in capsys.readouterr().err


def test_a_ci_review_needs_the_pull_request_settings(
    branch: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert command.review("gemini", branch, env={"GEMINI_API_KEY": "k"}) == 2
    assert "missing environment variables: GITHUB_TOKEN" in capsys.readouterr().err


def test_the_policy_and_marker_names_come_from_the_config() -> None:
    config = parse_config(
        TOML + '[tiers]\n1 = ["src/crm_*"]\n[review]\n'
        'providers = { 1 = ["greptile", "gemini", "mine"] }\nblocking_severities = ["P1"]\n'
        '[review.reviewers.mine]\nkind = "openai"\nmodel = "m"\nbase_url = "https://x"\n'
    )
    policy = command.policy(config)
    assert policy.blocking == ("P1",) and policy.tiers == {1: ("src/crm_*",)}
    names = command.marker_names(config)
    assert names["gemini-finding"] == "Gemini" and names["mine-finding"] == "Mine"
    assert "greptile-finding" not in names and "openai-finding" not in names


def test_the_cli_runs_the_review_command(branch: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        main(
            [
                "review",
                "--provider",
                "gemini",
                "--trusted",
                str(branch),
                "--local",
                "--base",
                "main",
            ]
        )
        == 1
    )  # no key in the test environment
    assert "GEMINI_API_KEY" in capsys.readouterr().out


def test_the_workflow_learns_the_status_name_and_passes_the_key_by_one_name(
    branch: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert command.status_context("reflection", branch) == 0
    assert capsys.readouterr().out == "Reflection review\n"
    assert command.status_context("nobody", branch) == 2
    asked: list[str] = []
    _fake_model(monkeypatch, asked)
    env = {command.SHARED_KEY: "shared"}
    assert command.review("gemini", branch, local=True, base="main", env=env) == 0
    assert asked == ["shared"]

"""``review.max_rounds``: a cap on how many times one reviewer reviews one pull request.

States. A run is uncapped (``max_rounds = 0``, the default: nothing is counted and the
run is what it was), under the cap (reviewed), capped (nothing sent, the status fails and
says so) or asked for again (a maintainer's label lifts the cap for that one run).

Identity. A round is one review the workflow account posted on the pull request with this
reviewer's summary marker. Invariants: only the workflow account's reviews count; the
count never goes down with a new head; a capped run is never a pass and never silent.
"""

from __future__ import annotations

import itertools
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from review_helpers import PROVIDER, commit, run

from devloop.config import REVIEWED_FILES, ConfigError, parse_config
from devloop.review import command
from devloop.review import github as gh
from devloop.review import run as r
from devloop.review.model import Baseline, Effort, ReviewError, RoundCapReached, Settings

SETTINGS = Settings("k", "t", "o/r", "7", "c" * 40, "d" * 40)
BOT = {"login": "github-actions[bot]"}
BASE = "b" * 40
REVIEWS = "https://api.github.com/repos/o/r/pulls/7/reviews?per_page=100"
TOML = '[framework]\nversion = "0.3"\n'
REPLY = '{"summary": "Adds a sync.", "findings": [], "not_posted": []}'


def _summary(
    rid: int,
    head: str = "a" * 40,
    *,
    user: dict[str, str] = BOT,
    marker: str = "testbot-review",
    unreviewed: int = 0,
) -> dict[str, Any]:
    """Return a review whose body ends with a reviewer's summary marker."""
    tag = f"<!-- {marker} findings=0 posted=0 not_posted=0 unreviewed={unreviewed} base={BASE} -->"
    return {"id": rid, "user": user, "commit_id": head, "body": f"### Review\n\n{tag}"}


class Api:
    """Answer the reviews and statuses listings from fixtures, recording each request."""

    def __init__(self, reviews: list[Any], status: int = 200) -> None:
        """Keep the reviews listing and its HTTP status."""
        self.reviews, self.status = reviews, status
        self.sent: list[tuple[str, str, object]] = []

    def __call__(self, method: str, url: str, token: str, body: object) -> gh.Reply:
        """Return the listing the URL names."""
        self.sent.append((method, url, body))
        if "/reviews" in url:
            return gh.Reply(self.status, self.reviews)
        success = {"context": "Testbot review", "state": "success", "creator": BOT}
        return gh.Reply(200, [success])


def test_a_round_is_a_review_the_workflow_account_posted_with_this_reviewers_marker() -> None:
    """Other accounts, other reviewers and a review's later comment batches are not rounds."""
    reviews = [
        _summary(1),
        _summary(2, user={"login": "the-author"}),  # the marker text, but not the bot's
        _summary(3, marker="otherbot-review"),
        {"id": 4, "user": BOT, "commit_id": "a" * 40, "body": "Testbot: findings, part 2 of 2"},
        {"id": 5, "user": {"login": "the-author"}, "body": "<!-- testbot-review findings=9"},
        _summary(6, unreviewed=2),  # an incomplete review was still sent and paid for
        {"id": 7, "user": None, "body": None},
    ]
    assert gh.History(SETTINGS, PROVIDER, Api(reviews)).rounds() == 2
    assert gh.History(SETTINGS, PROVIDER, Api([])).rounds() == 0


def test_a_new_head_does_not_reset_the_count() -> None:
    """The count is per pull request: a force push leaves every earlier review in the list."""
    reviews = [_summary(n, head=f"{n:x}" * 40) for n in (1, 2, 3)]
    assert gh.History(SETTINGS, PROVIDER, Api(reviews)).rounds() == 3


def test_a_count_that_cannot_be_read_fails_the_check() -> None:
    with pytest.raises(ReviewError, match=r"refused to list reviews \(502\)"):
        gh.History(SETTINGS, PROVIDER, Api([], status=502)).rounds()


def test_the_count_and_the_last_review_share_one_listing() -> None:
    """A capped project pays no extra request for the count."""
    api = Api([_summary(1), _summary(2)])
    history = gh.History(SETTINGS, PROVIDER, api)
    assert history.rounds() == 2 and history.previous() == Baseline("a" * 40, BASE)
    assert history.rounds() == 2
    assert [url for _, url, _ in api.sent] == [
        REVIEWS,
        f"https://api.github.com/repos/o/r/commits/{'a' * 40}/statuses?per_page=100",
    ]


def test_an_uncapped_run_sends_the_requests_it_always_sent() -> None:
    """With no cap the history is read exactly as ``previous_review`` reads it."""
    reviews = [_summary(1), _summary(2)]
    before, after = Api(reviews), Api(reviews)
    assert (
        gh.previous_review(SETTINGS, PROVIDER, before)
        == gh.History(SETTINGS, PROVIDER, after).previous()
    )
    assert (
        after.sent
        == before.sent
        == [
            ("GET", REVIEWS, None),
            (
                "GET",
                f"https://api.github.com/repos/o/r/commits/{'a' * 40}/statuses?per_page=100",
                None,
            ),
        ]
    )
    assert Api([]).sent == [] and gh.History(SETTINGS, PROVIDER, Api([])).previous() is None


class Pull:
    """A pull request adding ``app.py`` on a real repository, reviewed with fakes."""

    def __init__(self, repo: Path) -> None:
        """Commit the pull request's file on top of the base."""
        self.base = run(repo, "rev-parse", "HEAD")
        (repo / "app.py").write_text("a = 1\nb = 2\n")
        self.head = commit(repo, "the pull request")
        self.asked: list[str] = []
        self.posted: list[tuple[str, list[dict[str, Any]]]] = []
        self.counted = 0

    def _ask(self, prompt: str, effort: Effort) -> str:
        """Record the prompt and answer with no findings."""
        self.asked.append(prompt)
        return REPLY

    def review(self, policy: r.Policy, rounds: int | None) -> str:
        """Run one review; ``rounds`` is the count GitHub would give (``None``: not passed)."""

        def count() -> int:
            """Return the count, noting that it was asked for."""
            self.counted += 1
            assert rounds is not None
            return rounds

        return r.run_review(
            Settings("k", "t", "o/r", "7", self.base, self.head),
            PROVIDER,
            policy,
            name="testbot",
            ask=self._ask,
            known=set,
            post=lambda body, comments: self.posted.append((body, comments)),
            rounds=None if rounds is None else count,
        )


def _policy(
    max_rounds: int = 0, again: bool = False, again_label: str = "review-again"
) -> r.Policy:
    """Return a policy reviewing the default file types, with the cap's settings."""
    return r.Policy(
        files=REVIEWED_FILES, max_rounds=max_rounds, again=again, again_label=again_label
    )


STATUS = (
    "Not reviewed: cap of 2 reviews reached (2 done); a maintainer can add the review-again label"
)


def test_a_capped_pull_request_sends_nothing_and_fails_with_a_named_status(repo: Path) -> None:
    pull = Pull(repo)
    with pytest.raises(RoundCapReached) as error:
        pull.review(_policy(max_rounds=2), rounds=2)
    assert pull.asked == [] and pull.posted == []
    assert error.value.status == STATUS and len(STATUS) <= 140
    assert "review.max_rounds is 2" in str(error.value)
    with pytest.raises(RoundCapReached, match="reviewed this pull request 5 time"):
        pull.review(_policy(max_rounds=2), rounds=5)
    assert pull.asked == []


def test_a_capped_run_is_a_failed_check_with_the_cap_as_its_status(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Not judged is not healthy: exit 1, the status text for the workflow, one annotation."""
    output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    pull = Pull(repo)
    assert r.outcome(PROVIDER, lambda: pull.review(_policy(max_rounds=2), rounds=2)) == 1
    assert output.read_text() == f"status_description={STATUS}\n"
    printed = capsys.readouterr().out
    assert printed.startswith("::error title=Testbot review::Nothing was sent to Testbot")
    assert printed.count("\n") == 1


def test_under_the_cap_the_review_runs(repo: Path) -> None:
    pull = Pull(repo)
    assert pull.review(_policy(max_rounds=2), rounds=1) == (
        "Posted Testbot review with 0 finding thread(s)."
    )
    assert len(pull.asked) == 1 and len(pull.posted) == 1


def test_the_label_for_one_more_review_lifts_the_cap_without_counting(repo: Path) -> None:
    pull = Pull(repo)
    pull.review(_policy(max_rounds=2, again=True), rounds=9)
    assert len(pull.asked) == 1 and len(pull.posted) == 1 and pull.counted == 0


def test_the_status_names_the_configured_label_on_one_line(repo: Path) -> None:
    pull = Pull(repo)
    with pytest.raises(RoundCapReached) as error:
        pull.review(_policy(max_rounds=1, again_label="one more\nplease"), rounds=1)
    assert error.value.status.endswith("a maintainer can add the one more please label")


def test_with_no_cap_nothing_is_counted_and_the_review_is_byte_for_byte_the_same(
    repo: Path,
) -> None:
    """The default: the same prompt and the same post as a run that knows no cap."""
    pull = Pull(repo)
    assert pull.review(_policy(), rounds=None) == pull.review(_policy(), rounds=99)
    assert pull.counted == 0
    assert pull.asked[0] == pull.asked[1] and pull.posted[0] == pull.posted[1]
    body, comments = pull.posted[1]
    assert comments == []
    assert body.endswith(
        f"<!-- testbot-review findings=0 posted=0 not_posted=0 unreviewed=0 base={pull.base} -->"
    )
    assert "cap" not in body.lower() and "round" not in body.lower()


def test_a_pull_request_with_nothing_to_review_passes_without_counting(repo: Path) -> None:
    """A run that would send nothing anyway is not a round and is not capped."""
    base = run(repo, "rev-parse", "HEAD")
    (repo / "guide.md").write_text("words\n")
    head = commit(repo, "docs only")
    said = r.run_review(
        Settings("k", "t", "o/r", "7", base, head),
        PROVIDER,
        _policy(max_rounds=1),
        name="testbot",
        ask=lambda prompt, effort: pytest.fail("asked"),
        known=set,
        post=lambda body, comments: pytest.fail("posted"),
        rounds=lambda: pytest.fail("counted"),
    )
    assert said == "No reviewed file types changed; nothing for Testbot to review."


def test_the_cap_comes_from_the_config_and_the_label_from_the_workflow() -> None:
    assert parse_config(TOML).review.max_rounds == 0
    default = command.policy(parse_config(TOML))
    assert (default.max_rounds, default.again, default.again_label) == (0, False, "review-again")
    config = parse_config(TOML + "[review]\nmax_rounds = 3\n")
    capped = command.policy(config, again=True, again_label="once-more")
    assert (capped.max_rounds, capped.again, capped.again_label) == (3, True, "once-more")
    assert command.policy(config, again_label="").again_label == "review-again"


@pytest.mark.parametrize("value", ["-1", "true", '"3"', "1.5"])
def test_a_cap_that_is_not_a_count_is_rejected(value: str) -> None:
    with pytest.raises(ConfigError, match=r"review\.max_rounds"):
        parse_config(TOML + f"[review]\nmax_rounds = {value}\n")


# The workflow: who may ask for one more review, and that the approval gate comes first.

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "review.yml"
APPROVAL, AGAIN = "ai-review-approved", "review-again"
MEMBERS = ("OWNER", "MEMBER", "COLLABORATOR")
OUTSIDERS = ("CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER", "NONE", "")
ACTIONS = ("opened", "synchronize", "reopened", "ready_for_review", "labeled")


def _jobs() -> dict[str, Any]:
    """Return the jobs of the shared review workflow."""
    jobs: dict[str, Any] = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    return jobs


def _decide(
    tmp_path: Path, association: str, action: str, label: str, **env: str
) -> dict[str, str]:
    """Run the gate's own script for one event; return what it wrote to its outputs."""
    script = next(s for s in _jobs()["gate"]["steps"] if s.get("id") == "decide")["run"]
    output = tmp_path / "output"
    output.write_text("", encoding="utf-8")
    settings = {
        "GATE": "members",
        "APPROVAL_LABEL": APPROVAL,
        "AGAIN_LABEL": AGAIN,
        "TRUSTED_BOTS": "dependabot[bot]",
        "AUTHOR": "someone",
        "ASSOCIATION": association,
        "ACTION": action,
        "LABEL": label if action == "labeled" else "",
    }
    done = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        env={"PATH": "/usr/bin:/bin", "GITHUB_OUTPUT": str(output), **settings, **env},
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())


def test_the_label_reviews_a_members_pull_request_once_more(tmp_path: Path) -> None:
    for association in MEMBERS:
        found = _decide(tmp_path, association, "labeled", AGAIN)
        assert found == {"run": "true", "reason": "member", "again": "true"}, association
    bot = _decide(tmp_path, "NONE", "labeled", AGAIN, AUTHOR="dependabot[bot]")
    assert bot == {"run": "true", "reason": "trusted-bot", "again": "true"}
    everyone = _decide(tmp_path, "NONE", "labeled", AGAIN, GATE="everyone")
    assert everyone == {"run": "true", "reason": "open", "again": "true"}


def test_on_an_outside_pull_request_the_label_alone_spends_nothing(tmp_path: Path) -> None:
    """The approval gate comes first: no review, no key, and no status is touched."""
    for association in OUTSIDERS:
        found = _decide(tmp_path, association, "labeled", AGAIN)
        assert found == {"run": "false", "reason": "other-label", "again": "false"}, association
    waiting = _jobs()["gate"]["steps"][2]
    assert waiting["if"].startswith("steps.decide.outputs.reason == 'waiting'")


def test_the_cap_is_lifted_only_by_the_label_event_and_never_by_a_push(tmp_path: Path) -> None:
    """For every author, event and label: again is the label's own event on a reviewed run."""
    labels = (APPROVAL, AGAIN, "large-pr-approved")
    for association, action, label in itertools.product((*MEMBERS, *OUTSIDERS), ACTIONS, labels):
        found = _decide(tmp_path, association, action, label)
        asked = action == "labeled" and label == AGAIN and association in MEMBERS
        assert found["again"] == str(asked).lower(), (association, action, label)
        if association in OUTSIDERS:  # still gated: only the approval label reviews it
            approved = action == "labeled" and label == APPROVAL
            assert found["run"] == str(approved).lower(), (association, action, label)
            assert (found["reason"] == "approved") == approved


def test_the_review_step_is_told_of_the_label_from_fields_github_sets() -> None:
    """An approved outside pull request passes the cap only while it carries the label."""
    jobs = _jobs()
    assert jobs["gate"]["outputs"]["again"] == "${{ steps.decide.outputs.again }}"
    env = next(s for s in jobs["review"]["steps"] if s.get("id") == "review")["env"]
    assert env["DEVLOOP_REVIEW_AGAIN"] == (
        "${{ needs.gate.outputs.again == 'true' || (needs.gate.outputs.reason == 'approved' "
        "&& contains(github.event.pull_request.labels.*.name, inputs.again_label)) }}"
    )
    assert env["DEVLOOP_AGAIN_LABEL"] == "${{ inputs.again_label }}"
    inputs = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))[True]["workflow_call"]["inputs"]
    assert inputs["again_label"]["default"] == AGAIN == command.DEFAULT_AGAIN_LABEL
    decide = next(s for s in jobs["gate"]["steps"] if s.get("id") == "decide")
    assert "labels" not in str(decide["env"]).lower().replace("_label", "")


class _History:
    """A pull request this reviewer has reviewed once; the count is noted when asked for."""

    counted = 0

    def __init__(self, settings: Settings, provider: object) -> None:
        """Take what the real history takes."""

    def rounds(self) -> int:
        """Return one review, noting the request."""
        type(self).counted += 1
        return 1

    def previous(self) -> Baseline | None:
        """Return no completed review, so a full review is due."""
        return None


@pytest.mark.parametrize(
    ("toml", "variables", "code", "asked", "counted"),
    [
        ("[review]\nmax_rounds = 1\n", {}, 1, 0, 1),
        ("[review]\nmax_rounds = 1\n", {"DEVLOOP_REVIEW_AGAIN": "false"}, 1, 0, 1),
        ("[review]\nmax_rounds = 1\n", {"DEVLOOP_REVIEW_AGAIN": "true"}, 0, 1, 0),
        ("[review]\nmax_rounds = 2\n", {}, 0, 1, 1),
        ("", {}, 0, 1, 0),
    ],
    ids=["capped", "capped-other-event", "again", "under", "no-cap"],
)
def test_a_ci_run_applies_the_cap_from_the_trusted_config_and_the_workflows_variables(
    repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    toml: str,
    variables: dict[str, str],
    code: int,
    asked: int,
    counted: int,
) -> None:
    """End to end through ``devloop review``: the status the workflow will post."""
    (repo / "devloop.toml").write_text(TOML + toml)
    base = commit(repo, "config")
    (repo / "app.py").write_text("a = 1\nb = 2\n")
    head = commit(repo, "the pull request")
    prompts: list[str] = []
    posts: list[str] = []

    def asker(*_: object) -> r.Ask:
        """Return a model that records its prompts and reports nothing."""

        def ask(prompt: str, effort: Effort) -> str:
            """Record the prompt and answer."""
            prompts.append(prompt)
            return REPLY

        return ask

    def post(settings: Settings, provider: object, body: str, comments: object) -> None:
        """Record the review that would be posted."""
        posts.append(body)

    monkeypatch.setattr(_History, "counted", 0)
    monkeypatch.setattr(gh, "History", _History)
    monkeypatch.setattr(gh, "post_review", post)
    monkeypatch.setattr(command, "asker", asker)
    output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    env = {
        "GEMINI_API_KEY": "k",
        "GITHUB_TOKEN": "t",
        "GITHUB_REPOSITORY": "o/r",
        "PR_NUMBER": "7",
        "BASE_SHA": base,
        "HEAD_SHA": head,
        "DEVLOOP_AGAIN_LABEL": "once-more",
        **variables,
    }
    assert command.review("gemini", repo, env=env) == code
    assert (len(prompts), len(posts), _History.counted) == (asked, asked, counted)
    said = output.read_text() if output.exists() else ""
    if code:
        assert said == (
            "status_description=Not reviewed: cap of 1 reviews reached (1 done); "
            "a maintainer can add the once-more label\n"
        )
        assert "::error title=Gemini review::Nothing was sent to Gemini" in capsys.readouterr().out
    else:
        assert said == ""

"""``devloop labels``: every review finding has a labelled reply before the status passes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from review_helpers import PROVIDER, pull

from devloop.review import feedback as fb
from devloop.review import labels as lb
from devloop.review.github import Reply
from devloop.review.model import DiffLines, Finding
from devloop.review.threads import anchor

REVIEWERS = {"testbot-finding": "Testbot"}
PREFIXES = ("accepted", "rejected")
HEAD = "a" * 40


def _bot() -> dict[str, Any]:
    """Return the first comment of a thread the bot opened for one finding."""
    comment = anchor(
        PROVIDER, Finding("P2", "app.py", 3, "Leak", "b"), {"app.py": DiffLines(right={3})}
    )
    assert comment is not None
    return {"body": comment["body"], "url": "https://g/1", "author": {"login": "github-actions"}}


def _thread(*replies: str, resolved: bool = True) -> dict[str, Any]:
    """Return a finding thread with owner replies ``replies``."""
    comments = [_bot()] + [
        {"body": r, "authorAssociation": "OWNER", "author": {"login": "owner"}} for r in replies
    ]
    return {"id": "T", "isResolved": resolved, "path": "app.py", "comments": comments}


def test_every_finding_thread_needs_a_labelled_reply_resolved_or_not() -> None:
    threads = [_thread("accepted: fixed"), _thread(), _thread("thanks", resolved=False)]
    state, description, lines = lb.verdict(lb.findings(threads, REVIEWERS, PREFIXES), PREFIXES)
    assert state == "failure"
    assert description == "2 of 3 finding thread(s) need a accepted: or rejected: reply"
    assert lines == [
        "- Testbot finding on app.py (resolved): https://g/1",
        "- Testbot finding on app.py (open): https://g/1",
    ]
    ok = lb.verdict(lb.findings([_thread("rejected: no")], REVIEWERS, PREFIXES), PREFIXES)
    assert ok[0] == "success" and ok[1].startswith("All 1 finding thread(s)")
    assert lb.verdict([], PREFIXES)[:2] == ("success", "No review findings on this pull request")


STATUS = [{"context": "Testbot review", "state": "success"}]
DONE = [{"name": "Greptile Review", "status": "completed", "conclusion": "success"}]


@pytest.mark.parametrize(
    ("statuses", "checks", "reasons"),
    [
        (STATUS, DONE, []),
        ([], DONE, ["Waiting for Testbot review"]),
        ([{"context": "Testbot review", "state": "pending"}], DONE, ["Waiting for Testbot review"]),
        (
            [{"context": "Testbot review", "state": "error"}],
            DONE,
            ["Testbot review did not finish"],
        ),
        (STATUS, [], ["Waiting for Greptile Review"]),
        (STATUS, [{**DONE[0], "status": "in_progress"}], ["Waiting for Greptile Review"]),
        (STATUS, [{**DONE[0], "conclusion": "cancelled"}], ["Greptile Review did not finish"]),
    ],
    ids=["done", "no-status", "pending", "error", "no-check", "running", "cancelled"],
)
def test_a_review_that_has_not_finished_is_never_a_pass(
    statuses: list[dict[str, str]], checks: list[dict[str, str]], reasons: list[str]
) -> None:
    assert lb.waiting_for(statuses, checks, ["Testbot review"], ["Greptile Review"]) == reasons


class Api:
    """A fake GitHub: a PR whose head has ``statuses``, with ``threads``; records posts."""

    def __init__(
        self,
        threads: list[dict[str, Any]],
        statuses: list[dict[str, str]],
        fail: str = "",
        then: list[dict[str, Any]] | None = None,
    ) -> None:
        """Keep what the API answers; ``fail`` names a URL part that answers 403.

        ``then`` is what the threads become once the first verdict has been posted, as
        when a reply lands while the check is running.
        """
        self.threads, self.statuses, self.fail = threads, statuses, fail
        self.then = then
        self.fail_after_verdict = ""
        self.posted: list[tuple[str, str]] = []
        #: What each read of the pull request answers, in turn (the last one repeats),
        #: the branches of this repository headed by its commit, and every GET made.
        self.pulls: list[dict[str, object]] = [pull(HEAD)]
        self.shared: object = []
        self.gets: list[str] = []

    def __call__(self, method: str, url: str, token: str, body: object) -> Reply:
        """Answer one request."""
        if self.fail and self.fail in url:
            return Reply(403, {})
        if method == "POST" and "/statuses/" in url:
            assert isinstance(body, dict)
            self.posted.append((url.rsplit("/", 1)[1], body["state"]))
            if self.then is not None and body["state"] != "pending":
                self.threads, self.then = self.then, None
            if self.fail_after_verdict and body["state"] != "pending":
                self.fail, self.fail_after_verdict = self.fail_after_verdict, ""
            return Reply(201, {})
        if method == "GET":
            self.gets.append(url)
        if url.endswith("/pulls/7"):
            return Reply(200, self.pulls.pop(0) if len(self.pulls) > 1 else self.pulls[0])
        if url.endswith("/branches-where-head"):
            return Reply(200, self.shared)
        if "/status?" in url:
            return Reply(200, {"statuses": self.statuses})
        nodes = [{**t, "comments": {"nodes": t["comments"], "pageInfo": {}}} for t in self.threads]
        pr = {"reviewThreads": {"nodes": nodes, "pageInfo": {"hasNextPage": False}}}
        return Reply(200, {"data": {"repository": {"pullRequest": pr}}})


def _run(
    api: Api, tmp_path: Path, wait_for: tuple[str, ...] = ("Testbot review",)
) -> tuple[int, Path]:
    """Run the check against ``api``; return the exit code and the outputs file."""
    out = tmp_path / "out.txt"
    gh = lb.Github("t", "o/r", send=api, sleep=lambda _: None)
    code = lb.run(gh, 7, "run", REVIEWERS, PREFIXES, wait_for, event_sha="e" * 40, outputs=str(out))
    return code, out


def test_the_head_goes_pending_first_then_gets_the_verdict(tmp_path: Path) -> None:
    api = Api([_thread("accepted: fixed")], STATUS)
    code, out = _run(api, tmp_path)
    assert code == 0 and api.posted == [(HEAD, "pending"), (HEAD, "success")]
    assert out.read_text() == f"sha={HEAD}\nposted=true\n"


def test_an_unlabelled_thread_fails_and_a_running_review_keeps_it_pending(tmp_path: Path) -> None:
    api = Api([_thread()], STATUS)
    code, out = _run(api, tmp_path)
    assert api.posted[-1] == (HEAD, "failure")
    # The status carries the verdict; the job succeeded, because it checked and posted.
    assert code == 0 and "posted=true" in out.read_text()
    waiting = Api([_thread("accepted: fixed")], [])
    assert _run(waiting, tmp_path)[0] == 0 and waiting.posted[-1] == (HEAD, "pending")


def test_a_reply_that_lands_while_the_check_runs_is_in_the_final_status(tmp_path: Path) -> None:
    """Runs overlap: the last status a run leaves is for the threads as they are at its end."""
    answered = Api([_thread()], STATUS, then=[_thread("accepted: fixed")])
    assert _run(answered, tmp_path)[0] == 0
    assert answered.posted == [(HEAD, "pending"), (HEAD, "failure"), (HEAD, "success")]
    reopened = Api([_thread("accepted: fixed")], STATUS, then=[_thread()])
    assert _run(reopened, tmp_path)[0] == 0
    assert reopened.posted == [(HEAD, "pending"), (HEAD, "success"), (HEAD, "failure")]


def test_a_second_read_that_fails_never_leaves_the_pass_standing(tmp_path: Path) -> None:
    """GitHub answering the first read and refusing the second is a failed check, not a pass."""
    api = Api([_thread("accepted: fixed")], STATUS)
    api.fail_after_verdict = "graphql"
    with pytest.raises(fb.FeedbackError):
        _run(api, tmp_path)
    assert api.posted == [(HEAD, "pending"), (HEAD, "success"), (HEAD, "failure")]
    assert "posted=true" in (tmp_path / "out.txt").read_text()


def test_a_review_that_finishes_while_the_check_runs_ends_the_wait(tmp_path: Path) -> None:
    """Pending on the first read and done on the second: the status ends as the verdict."""
    api = Api([_thread("accepted: fixed")], [])
    statuses = api.statuses

    def finish(method: str, url: str, token: str, body: object) -> Reply:
        """Answer as the API does, with the review finished once pending is posted twice."""
        reply = api(method, url, token, body)
        if api.posted[-1:] == [(HEAD, "pending")] and len(api.posted) == 2:
            api.statuses = STATUS
        return reply

    gh = lb.Github("t", "o/r", send=finish, sleep=lambda _: None)
    out = tmp_path / "out.txt"
    assert lb.run(gh, 7, "run", REVIEWERS, PREFIXES, ("Testbot review",), outputs=str(out)) == 0
    assert statuses == [] and api.posted[-1] == (HEAD, "success")


def test_an_unchanged_answer_is_posted_once(tmp_path: Path) -> None:
    """The second read posts nothing when it agrees with the first."""
    api = Api([_thread()], STATUS)
    _run(api, tmp_path)
    assert api.posted == [(HEAD, "pending"), (HEAD, "failure")]


def test_a_failed_read_posts_a_failure_never_a_pass(tmp_path: Path) -> None:
    api = Api([], STATUS, fail="graphql")
    with pytest.raises(fb.FeedbackError):
        _run(api, tmp_path)
    assert api.posted == [(HEAD, "pending"), (HEAD, "failure")]


def test_an_unreadable_pull_request_fails_the_event_head(tmp_path: Path) -> None:
    api = Api([], STATUS, fail="/pulls/7")
    with pytest.raises(fb.FeedbackError):
        _run(api, tmp_path)
    assert api.posted == [("e" * 40, "failure")]


def test_the_command_needs_its_settings(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "devloop.toml").write_text('[framework]\nversion = "0.3"\n')
    assert (
        lb.main(tmp_path, {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "o/r", "PR_NUMBER": "x"}) == 2
    )
    assert "numeric PR_NUMBER" in capsys.readouterr().err


def test_a_check_name_is_sent_url_encoded() -> None:
    sent: list[str] = []

    def api(method: str, url: str, token: str, body: object) -> Reply:
        """Record the URL and answer with no runs."""
        sent.append(url)
        return Reply(200, {"statuses": [], "check_runs": []})

    lb.Github("t", "o/r", send=api, sleep=lambda _: None).reviews(HEAD, ["Lint & Test (C++)"])
    assert sent[-1].endswith("check_name=Lint%20%26%20Test%20%28C%2B%2B%29&filter=latest")


FORK = "stranger/r"
OTHER = "b" * 40


def _fork(threads: list[dict[str, Any]], head_repo: str | None = FORK, shared: int = 0) -> Api:
    """Return the fake for a fork's pull request whose head ``shared`` branches here head."""
    api = Api(threads, STATUS)
    api.pulls = [pull(HEAD, head_repo)]
    api.shared = [{"name": f"branch-{n}"} for n in range(shared)]
    return api


@pytest.mark.parametrize("head_repo", [FORK, None], ids=["fork", "deleted-fork"])
def test_a_fork_whose_head_is_a_branch_head_here_gets_no_status_at_all(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], head_repo: str | None
) -> None:
    """No pending and no verdict lands on the shared commit; the run says why and succeeds.

    The fork's pull request has no finding threads, so unguarded it would post a success
    over the failure the branch's own pull request earned.
    """
    api = _fork([], head_repo, shared=1)
    code, out = _run(api, tmp_path)
    assert code == 0 and api.posted == []
    assert not out.exists() or out.read_text() == ""
    said = capsys.readouterr().out
    assert said.startswith("::notice title=Review labels::No status written") and "#7" in said


@pytest.mark.parametrize("head_repo", [FORK, None], ids=["fork", "deleted-fork"])
def test_a_fork_whose_head_no_branch_here_shares_is_checked_as_before(
    tmp_path: Path, head_repo: str | None
) -> None:
    """The guard asks about the commit it then posts on, and the statuses are as today."""
    api = _fork([_thread("accepted: fixed")], head_repo)
    code, out = _run(api, tmp_path)
    assert code == 0 and api.posted == [(HEAD, "pending"), (HEAD, "success")]
    assert out.read_text() == f"sha={HEAD}\nposted=true\n"
    assert [u for u in api.gets if "branches-where-head" in u] == [
        f"https://api.github.com/repos/o/r/commits/{HEAD}/branches-where-head"
    ]


def test_a_same_repository_pull_request_costs_no_extra_read_or_write(tmp_path: Path) -> None:
    """Exactly today's requests, in today's order: one head read, pending, then the verdict."""
    api = Api([_thread("accepted: fixed")], STATUS)
    api.shared = [{"name": "its-own-branch"}]  # its head is a branch head here, as always
    assert _run(api, tmp_path)[0] == 0
    assert api.posted == [(HEAD, "pending"), (HEAD, "success")]
    assert [u.removeprefix("https://api.github.com/") for u in api.gets] == [
        "repos/o/r/pulls/7",
        f"repos/o/r/commits/{HEAD}/status?per_page=100",
        f"repos/o/r/commits/{HEAD}/status?per_page=100",
    ]


@pytest.mark.parametrize("broken", ["missing", "refused", "not-a-list"])
def test_a_guard_that_could_not_check_fails_the_run_and_never_passes(
    tmp_path: Path, broken: str
) -> None:
    """Could not check is a failed run: no pending, no success, at most a failure."""
    api = _fork([], shared=1)
    if broken == "missing":  # the reply names no base repository to compare with
        api.pulls = [{"head": {"sha": HEAD, "repo": {"full_name": FORK}}}]
    elif broken == "refused":
        api.fail = "branches-where-head"
    else:
        api.shared = {"message": "no"}
    with pytest.raises(fb.FeedbackError):
        _run(api, tmp_path)
    assert api.posted == [("e" * 40, "failure")]


def test_the_commit_judged_is_the_commit_written_when_the_head_moves(tmp_path: Path) -> None:
    """The head is read once, so a push during the run cannot move the status elsewhere."""
    api = _fork([_thread("accepted: fixed")])
    api.pulls = [pull(HEAD, FORK), pull(OTHER, FORK)]
    assert _run(api, tmp_path)[0] == 0
    assert len([u for u in api.gets if u.endswith("/pulls/7")]) == 1
    assert [u.split("/")[-2] for u in api.gets if "branches-where-head" in u] == [HEAD]
    assert {sha for sha, _ in api.posted} == {HEAD}

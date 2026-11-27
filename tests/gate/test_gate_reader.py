"""``devloop gate`` against a fake GitHub: the guards, every failed read, and what is posted.

Invariants (devloop.gate.reader). Nothing is posted for a pull request that is closed,
goes into another branch, or is a fork's on a head a branch here also heads. Pending is
posted once, after those guards. Whatever cannot be read ends as a failure, never a pass.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from devloop.cli import main as cli
from devloop.config import ConfigError, Gate, parse_config
from devloop.gate import reader, rules
from devloop.review import feedback as fb
from devloop.review.github import Reply

BOT = "github-actions[bot]"
HEAD, BASE, MOVED = "a" * 40, "c" * 40, "b" * 40
CONFIG = (
    '[framework]\nversion = "0.1"\n[labels]\nwait_for = ["Alpha review"]\n'
    '[gate]\nchecks = [{ name = "Outside Review", app = 4242 }]\n'
)
#: Strict needs the Alpha review and Review labels statuses and the Outside Review check.
GATE = rules.rules(parse_config(CONFIG))
CHECKS = f"/commits/{HEAD}/check-runs?check_name=Outside%20Review&app_id=4242"
OUTSIDER: dict[str, Any] = {
    "author_association": "CONTRIBUTOR",
    "user": {"login": "stranger", "type": "User"},
}


def _status(context: str, state: str, creator: str = BOT, n: int = 1) -> dict[str, Any]:
    """Return one commit status as the API returns it; a higher ``n`` is newer."""
    return {"context": context, "state": state, "creator": {"login": creator}, "id": n}


def _check(conclusion: str, app: int = 4242) -> dict[str, Any]:
    """Return one finished check run named Outside Review."""
    run = {"name": "Outside Review", "status": "completed", "conclusion": conclusion, "id": 1}
    return {**run, "app": {"id": app}}


def _file(name: str = "docs/a.md", status: str = "modified") -> dict[str, Any]:
    """Return one changed file, as the comparison lists it."""
    return {"filename": name, "status": status}


CORE = _file("src/app/x.py")
PASSED = [_status("Alpha review", "success"), _status("Review labels", "success")]


class Hub:
    """Canned answers of the API for pull request 7 of o/r, and the statuses posted."""

    def __init__(self, files: list[dict[str, Any]], *, fork: bool = False, **pull: object) -> None:
        """Start with a member's open pull request into the default branch."""
        base_repo = {"full_name": "o/r", "default_branch": "main"}
        self.pull: dict[str, Any] = {
            "number": 7,
            "state": "open",
            "base": {"ref": "main", "sha": BASE, "repo": base_repo},
            "head": {"sha": HEAD, "repo": {"full_name": "fork/r" if fork else "o/r"}},
            "user": {"login": "maintainer", "type": "User"},
            "author_association": "OWNER",
            "changed_files": len(files),
            "commits": 2,
            **pull,
        }
        self.files = files
        self.modes = {f["filename"]: "100644" for f in files}
        self.on_default = ["docs/a.md", "docs/README.md"]
        self.compared: object = 2
        self.answers: dict[str, object] = {}
        self.statuses: list[dict[str, Any]] = []
        self.checks: list[dict[str, Any]] = []
        self.branch_heads: list[str] = []
        self.posted: list[tuple[str, str, str]] = []
        self.asked: list[str] = []
        self.fail: str | None = None
        self.pull_reads = 0

    def get(self, path: str) -> object:
        """Answer one read."""
        self.asked.append(path)
        if self.fail and self.fail in path:
            raise fb.FeedbackError(f"cannot read {path}")
        for part, answer in self.answers.items():
            if part in path:
                return answer
        page = int(path.rsplit("page=", 1)[-1]) if "page=" in path else 1
        if path == "repos/o/r/pulls/7":
            self.pull_reads += 1
            return self.pull
        if path == f"repos/o/r/compare/{BASE}...{HEAD}?per_page=1&page=1":
            # As the live API answers: every file at once, at most 300, never paged.
            return {"files": self.files[:300], "total_commits": self.compared}
        if path == f"repos/o/r/git/trees/{BASE}?recursive=1":
            tree = [{"path": name, "mode": "100644"} for name in self.on_default]
            return {"tree": tree, "truncated": False}
        if path == f"repos/o/r/git/trees/{HEAD}?recursive=1":
            tree = [{"path": p, "mode": m, "type": "blob"} for p, m in self.modes.items()]
            return {"tree": tree, "truncated": False}
        if path == f"repos/o/r/commits/{HEAD}/statuses?per_page=100&page={page}":
            return self.statuses[(page - 1) * 100 : page * 100]
        if path == f"repos/o/r{CHECKS}&per_page=100&page={page}":
            return {"check_runs": self.checks[(page - 1) * 100 : page * 100]}
        if path == f"repos/o/r/commits/{HEAD}/branches-where-head":
            return [{"name": name} for name in self.branch_heads]
        raise AssertionError(path)

    def post(self, sha: str, state: str, description: str) -> None:
        """Record one status."""
        self.posted.append((sha, state, description))

    def run(self, gate: Gate = GATE) -> int:
        """Evaluate the pull request once."""
        return reader.evaluate(self, "o/r", 7, gate)

    @property
    def states(self) -> list[str]:
        """Return the states posted, in order; every one is on the head that was read."""
        assert all(sha == HEAD for sha, _, _ in self.posted)
        return [state for _, state, _ in self.posted]

    @property
    def last(self) -> tuple[str, str]:
        """Return the state and description of the newest status."""
        return self.posted[-1][1:]


def _reviewed(*files: dict[str, Any], fork: bool = False, **pull: object) -> Hub:
    """Return a pull request on whose head every review has passed."""
    hub = Hub(list(files), fork=fork, **pull)
    hub.statuses, hub.checks = list(PASSED), [_check("success")]
    return hub


def test_a_documents_pull_request_ends_green_with_no_review() -> None:
    """Pending first, then success for the light lane."""
    hub = Hub([_file()])
    assert hub.run() == 0
    assert hub.states == ["pending", "success"] and "Light lane" in hub.last[1]


def test_a_documents_pull_request_fails_when_a_blocking_status_failed() -> None:
    """A finding a reviewer did post still needs its label."""
    hub = Hub([_file()])
    hub.statuses = [_status("Review labels", "failure")]
    assert hub.run() == 0
    assert hub.last == ("failure", "Light lane: failed: Review labels")


def test_a_core_pull_request_waits_then_passes_when_the_reviews_are_in(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Strict: pending, naming each review, until every status and check passed."""
    hub = Hub([CORE])
    assert hub.run() == 0
    waits = "waiting for Alpha review, Review labels, Outside Review"
    assert hub.last == ("pending", f"Strict lane (files other than documents change): {waits}")
    hub.statuses, hub.checks = list(PASSED), [_check("success")]
    hub.posted.clear()
    assert hub.run() == 0
    assert hub.states == ["pending", "success"]
    # The log names every review and where it stands.
    out = capsys.readouterr().out
    assert "- Alpha review: waiting" in out and "- Outside Review: passed" in out


def test_each_status_is_read_under_its_own_name() -> None:
    """One review's status never stands in for another's."""
    hub = _reviewed(CORE)
    hub.statuses = [_status("Alpha review", "failure"), _status("Review labels", "success")]
    hub.run()
    assert hub.last[1].endswith("failed: Alpha review")
    hub.statuses = [_status("Alpha review", "success"), _status("Review labels", "failure")]
    hub.run()
    assert hub.last[1].endswith("failed: Review labels")


def test_a_status_counts_only_from_the_workflow_account_and_a_check_only_from_its_app() -> None:
    """Anyone with write access can post a status; an app can name its check anything."""
    forged = _reviewed(CORE)
    forged.statuses = [_status("Alpha review", "success", creator="someone"), PASSED[1]]
    forged.run()
    assert forged.last[0] == "pending" and forged.last[1].endswith("waiting for Alpha review")
    other_app = _reviewed(CORE)
    other_app.checks = [_check("success", app=7)]
    other_app.run()
    assert other_app.last[0] == "pending" and other_app.last[1].endswith("Outside Review")
    assert any(f"{CHECKS}&per_page=100&page=1" in path for path in other_app.asked)


@pytest.mark.parametrize("broken", ["/compare/", "/git/trees/", "/statuses", "/check-runs"])
def test_whatever_cannot_be_read_is_a_failure_never_a_pass(broken: str) -> None:
    """An error becomes a failure and a failed run, not an older success left standing."""
    hub = _reviewed(CORE)
    hub.fail = broken
    assert hub.run() == 1
    assert hub.last == ("failure", "The merge gate could not check this head; re-run it")


@pytest.mark.parametrize("bad", [None, "text", [1, 2], [{"context": None}, 7]])
@pytest.mark.parametrize("where", ["/statuses", "/check-runs", "/compare/", "/git/trees/"])
def test_an_answer_of_the_wrong_shape_is_a_failure_not_a_crash(bad: object, where: str) -> None:
    """Whatever the API sends, the head ends as a failure, never left pending."""
    hub = _reviewed(_file())
    hub.answers[where] = bad
    assert hub.run() == 1
    assert hub.states == ["pending", "failure"]


def test_a_truncated_tree_or_a_tree_with_odd_entries_is_a_failure() -> None:
    """A tree that could not be read whole is not a tree."""
    trees: list[dict[str, Any]] = [
        {"tree": [], "truncated": True},
        {"tree": []},
        {"tree": [1], "truncated": False},
    ]
    for tree in trees:
        hub = Hub([_file()])
        hub.answers["/git/trees/"] = tree
        assert hub.run() == 1
        assert hub.last[0] == "failure"


def test_a_list_that_never_ends_is_a_failure() -> None:
    """More pages than the gate reads is "could not check", not a guess."""
    hub = Hub([_file()])
    hub.statuses = [_status("Review labels", "pending", n=i) for i in range(reader.MAX_PAGES * 100)]
    assert hub.run() == 1
    assert hub.last[0] == "failure"


def test_lists_are_read_to_their_last_page() -> None:
    """The review a head is waiting for is found however many statuses came after it."""
    files = [_file(f"docs/page-{i:03}.md") for i in range(99)] + [CORE, _file("docs/zz.md")]
    hub = _reviewed(*files)
    noise = [_status("Review labels", "pending", n=1000 + i) for i in range(150)]
    hub.statuses = [
        *noise,
        _status("Alpha review", "success", n=5),
        _status("Review labels", "success", n=2000),
    ]
    assert hub.run() == 0
    assert hub.last[0] == "success" and "reviews complete" in hub.last[1]
    assert any("statuses?per_page=100&page=2" in path for path in hub.asked)


def test_the_files_are_read_once_by_the_two_commits_never_by_the_number() -> None:
    """A list read by number belongs to whatever head the pull request has at that moment."""
    hub = Hub([CORE])
    hub.run()
    assert not [path for path in hub.asked if "/pulls/7/files" in path]
    assert len([path for path in hub.asked if f"/compare/{BASE}...{HEAD}" in path]) == 1
    # Only the reviews are read a second time: a run stays cheap.
    assert len([path for path in hub.asked if "/git/trees/" in path]) == 2


def test_a_symbolic_link_under_docs_is_not_a_document() -> None:
    """The kind of file is read from the tree, not guessed from its name."""
    hub = Hub([_file()])
    hub.modes["docs/a.md"] = "120000"
    hub.run()
    assert hub.last[0] == "pending" and "a document that is not a regular file" in hub.last[1]


def test_a_new_document_that_collides_by_case_with_one_on_the_default_branch_is_strict() -> None:
    """The default branch's own paths are read, so the case check is never skipped."""
    hub = Hub([_file("docs/readme.md", "added")])
    hub.run()
    assert hub.last[0] == "pending" and "only by case" in hub.last[1]


def test_a_list_shorter_than_the_pull_request_reports_is_not_complete() -> None:
    """The count the pull request reports is the one the list must reach."""
    short = Hub([_file()], changed_files=2)
    short.run()
    assert short.last[0] == "pending" and "not complete" in short.last[1]
    # The comparison lists at most 300 files: a longer change falls short of its count.
    long = Hub([_file(f"docs/page-{i:03}.md") for i in range(301)])
    assert long.run() == 0
    assert long.last[0] == "pending" and "not complete" in long.last[1]


@pytest.mark.parametrize(("compared", "reported"), [(3, 2), (1, 2), (None, None), (True, 1)])
def test_a_comparison_that_is_not_the_pull_requests_is_not_complete(
    compared: object, reported: object
) -> None:
    """Another base, or a count that is not a number: strict and flagged, never light."""
    hub = Hub([_file()], commits=reported)
    hub.compared = compared
    assert hub.run() == 0
    assert hub.last[0] == "pending" and "not complete" in hub.last[1]
    outsider = _reviewed(_file(), commits=reported, **OUTSIDER)
    outsider.compared = compared
    assert outsider.run() == 0
    assert outsider.last[0] == "failure" and "Not judged" in outsider.last[1]


# --- the guards: when nothing is posted ------------------------------------------------


@pytest.mark.parametrize("state", ["closed", "merged"])
def test_a_pull_request_that_is_not_open_gets_no_status(state: str) -> None:
    """Nothing is posted on a commit nobody is asking to merge."""
    hub = Hub([_file()], state=state)
    assert hub.run() == 0
    assert hub.posted == [] and hub.asked == ["repos/o/r/pulls/7"]


def test_only_a_pull_request_into_the_default_branch_is_judged() -> None:
    """The default branch is the repository's own, whatever it is called."""
    repo = {"full_name": "o/r", "default_branch": "trunk"}
    other = Hub([_file()], base={"ref": "main", "sha": BASE, "repo": repo})
    assert other.run() == 0
    assert other.posted == [] and other.asked == ["repos/o/r/pulls/7"]
    into = Hub([_file()], base={"ref": "trunk", "sha": BASE, "repo": repo})
    assert into.run() == 0
    assert into.states == ["pending", "success"]


@pytest.mark.parametrize("author", [OUTSIDER, {"author_association": "COLLABORATOR"}])
def test_a_forks_pull_request_on_a_head_a_branch_here_also_has_gets_no_status(
    author: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    """A status belongs to a commit: a fork must not move the ones on a branch's head."""
    hub = Hub([_file()], fork=True, **author)
    hub.branch_heads = ["feat/a-branch-here"]
    assert hub.run() == 0
    assert hub.posted == []
    assert "::notice title=Merge gate::No status written for pull request #7" in (
        capsys.readouterr().out
    )


def test_a_fork_whose_head_no_branch_here_has_is_judged_and_a_branch_here_needs_no_read() -> None:
    """The guard is the status guard's: only a fork costs the read of the branches."""
    fork = Hub([_file()], fork=True, **OUTSIDER)
    assert fork.run() == 0
    assert fork.states == ["pending", "success"]
    here = Hub([_file()], author_association="NONE", user={"login": "helper[bot]", "type": "Bot"})
    here.branch_heads = ["feat/its-own-branch"]
    assert here.run() == 0
    assert here.states == ["pending", "success"]
    assert not [path for path in here.asked if "branches-where-head" in path]


def test_a_forks_head_whose_branches_cannot_be_read_ends_as_a_failure() -> None:
    """Could not check is a failure on the head; pending is never written before the guard."""
    for answer in (None, {"not": "a list"}):
        hub = Hub([_file()], fork=True, **OUTSIDER)
        hub.fail = None if answer else "/branches-where-head"
        hub.answers = {"/branches-where-head": answer} if answer else {}
        assert hub.run() == 1
        assert hub.states == ["failure"]


@pytest.mark.parametrize("head", [None, {"repo": {"full_name": "o/r"}}, {"sha": 7}, {"sha": ""}])
def test_a_pull_request_with_no_head_commit_is_an_error_with_no_status(head: object) -> None:
    """There is no commit to post on: the failed run is all there is."""
    hub = Hub([_file()], head=head)
    with pytest.raises(fb.FeedbackError, match="names no head commit"):
        hub.run()
    assert hub.posted == []


MISSING = object()
REPO = {"full_name": "o/r", "default_branch": "main"}


@pytest.mark.parametrize("value", [MISSING, None, 7, "", ["open"]])
@pytest.mark.parametrize("key", ["state", "base.ref", "base.repo.default_branch"])
@pytest.mark.parametrize("otherwise", [{}, {"state": "closed"}, {"base.ref": "release"}])
def test_a_first_read_without_a_key_it_judges_by_is_could_not_check(
    key: str, value: object, otherwise: dict[str, str]
) -> None:
    """Not "nothing is due": a status that never arrives must show as a failure and a failed run.

    Even when another key says the pull request is closed or goes into another branch.
    """
    hub = Hub([_file()], base={"ref": "main", "sha": BASE, "repo": dict(REPO)})
    for path, new in ({**otherwise, key: value}).items():
        record = hub.pull
        *parents, last = path.split(".")
        for part in parents:
            record = record[part]
        if new is MISSING:
            del record[last]
        else:
            record[last] = new
    assert hub.run() == 1
    assert hub.states == ["failure"] and hub.asked == ["repos/o/r/pulls/7"]


@pytest.mark.parametrize("base", [None, "main", {"ref": "main", "sha": BASE, "repo": "o/r"}])
def test_a_first_read_whose_base_is_not_a_record_is_could_not_check(base: object) -> None:
    """A base of the wrong shape names no branch at all."""
    hub = Hub([_file()], base=base)
    assert hub.run() == 1
    assert hub.states == ["failure"]


@pytest.mark.parametrize("head", [MISSING, None, {}, {"sha": None}, {"sha": 7}, {"sha": ""}])
def test_a_second_read_without_a_head_commit_is_could_not_check_not_a_moved_head(
    head: object,
) -> None:
    """Only a record that names another commit says the head moved."""

    class Second(Hub):
        """Answers the pull request without a usable head from its second read on."""

        def get(self, path: str) -> object:
            """Return the record with its head replaced, or removed, after the first read."""
            answer = super().get(path)
            if not path.endswith("/pulls/7") or self.pull_reads < 2:
                return answer
            rest = {key: value for key, value in self.pull.items() if key != "head"}
            return rest if head is MISSING else {**rest, "head": head}

    hub = Second([_file()])
    assert hub.run() == 1
    assert hub.states == ["pending", "failure"]


def test_pending_is_written_once_and_only_after_the_guards() -> None:
    """The reads before the first status are the pull request and, for a fork, the branches."""

    class Noting(Hub):
        """Notes what had been read when each status was posted."""

        def post(self, sha: str, state: str, description: str) -> None:
            """Record the status with the reads made so far in place of its description."""
            super().post(sha, state, " ".join(self.asked))

    hub = Noting([CORE], fork=True, **OUTSIDER)
    assert hub.run() == 0
    assert hub.states == ["pending", "pending"]  # the marker, then the verdict "waiting for"
    assert hub.last[1].count("/statuses") == 1
    read_first = f"repos/o/r/pulls/7 repos/o/r/commits/{HEAD}/branches-where-head"
    assert hub.posted[0][2] == read_first


# --- who the author is, and a head that moves ------------------------------------------


def test_an_outside_authors_change_to_a_control_file_fails_through_the_whole_gate() -> None:
    """Who the author is reaches the verdict: a stranger is never judged as a member."""
    hub = _reviewed(_file("devloop.toml"), **OUTSIDER)
    assert hub.run() == 0
    assert hub.last[0] == "failure" and "Not judged (control files change)" in hub.last[1]
    assert _reviewed(_file("devloop.toml")).run() == 0


def test_an_outside_authors_core_pull_request_waits_for_a_check_that_really_ran() -> None:
    """Skipped by the app is not reviewed by the app."""
    hub = _reviewed(CORE, **OUTSIDER)
    hub.checks = [_check("skipped")]
    hub.run()
    assert hub.last[0] == "pending"
    member = _reviewed(CORE)
    member.checks = [_check("skipped")]
    member.run()
    assert member.last[0] == "success"


def test_a_head_that_moved_while_the_gate_was_reading_gets_no_verdict() -> None:
    """The new head's own run judges it."""

    class Moving(Hub):
        """A pull request whose head is another commit from its second read on."""

        def get(self, path: str) -> object:
            """Answer a different head once the pull request has been read."""
            answer = super().get(path)
            moved = path.endswith("/pulls/7") and self.pull_reads > 1
            return {**self.pull, "head": {"sha": MOVED}} if moved else answer

    hub = Moving([_file()])
    assert hub.run() == 0
    assert hub.states == ["pending"]


def test_a_verdict_that_changed_while_it_was_posted_is_posted_again() -> None:
    """A slow run does not leave its older answer over a newer one."""

    class Arrives(Hub):
        """The reviews arrive just after the first verdict was posted."""

        def post(self, sha: str, state: str, description: str) -> None:
            """Record the status; once the first verdict is out, the reviews land."""
            super().post(sha, state, description)
            if state == "pending" and "waiting" in description:
                self.statuses, self.checks = list(PASSED), [_check("success")]

    hub = Arrives([CORE])
    assert hub.run() == 0
    assert hub.states == ["pending", "pending", "success"]


def test_an_unexpected_error_ends_as_a_failure_and_prints_its_kind_not_its_text(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Only devloop's own errors are quoted: another error's text may hold what was read."""
    hub = Hub([_file()])
    hub.pull["changed_files"] = "s3cret"
    assert hub.run() == 1
    assert hub.states == ["pending", "failure"]
    assert capsys.readouterr().err.strip() == "devloop gate: could not check: ValueError"
    odd = Hub([CORE])
    odd.answers["/check-runs"] = []
    assert odd.run() == 1
    assert "FeedbackError: an answer from the API is not a record" in capsys.readouterr().err

    class Broken(Hub):
        """Answers the second read of the pull request with something that is not a record."""

        def get(self, path: str) -> object:
            """Return a list where a record is expected, from the second read on."""
            answer = super().get(path)
            return ["not", "a", "record"] if self.pull_reads > 1 and "/pulls/" in path else answer

    again = Broken([_file()])
    assert again.run() == 1
    assert again.states == ["pending", "failure"]


BAD: dict[str, tuple[object, ...]] = {
    "filename": (MISSING, None, 7, "", ["docs/a.md"]),
    "status": (MISSING, None, 1),
    "previous_filename": (7, ["docs/b.md"], {"a": 1}),
}


@pytest.mark.parametrize(("key", "value"), [(k, v) for k, bad in BAD.items() for v in bad])
def test_a_changed_file_whose_name_or_kind_is_not_text_is_could_not_check(
    key: str, value: object, capsys: pytest.CaptureFixture[str]
) -> None:
    """The record is refused where it is read: the rules only ever see text."""
    entry = {"filename": "docs/a.md", "status": "renamed", "previous_filename": "docs/b.md"}
    hub = Hub([entry])
    hub.files = [{k: v for k, v in {**entry, key: value}.items() if v is not MISSING}]
    assert hub.run() == 1
    assert hub.states == ["pending", "failure"]
    assert "a changed file's name or kind of change is not text" in capsys.readouterr().err


# --- the API client and the command ----------------------------------------------------


def test_the_client_posts_the_gates_own_status_and_refuses_an_error_answer() -> None:
    """One context, a short description, the run's link; a refusal is an error after retries."""
    sent: list[tuple[str, str, str, object]] = []
    answers = [Reply(200, {"ok": True}), Reply(201, {})]

    def send(method: str, url: str, token: str, body: object = None) -> Reply:
        """Record one request and answer the next canned reply."""
        sent.append((method, url, token, body))
        return answers.pop(0)

    waits: list[float] = []
    api = reader.Api("t0ken", "o/r", "May merge", "https://run/1", send=send, sleep=waits.append)
    assert api.get("repos/o/r/pulls/7") == {"ok": True}
    api.post(HEAD, "success", "x" * 200)
    assert sent[0] == ("GET", "https://api.github.com/repos/o/r/pulls/7", "t0ken", None)
    assert sent[1][:3] == ("POST", f"https://api.github.com/repos/o/r/statuses/{HEAD}", "t0ken")
    assert sent[1][3] == {
        "state": "success",
        "context": "May merge",
        "description": "x" * 140,
        "target_url": "https://run/1",
    }
    answers += [Reply(201, {})]
    reader.Api("t0ken", "o/r", "May merge", send=send, sleep=waits.append).post(
        HEAD, "pending", "x"
    )
    assert sent[2][3] == {"state": "pending", "context": "May merge", "description": "x"}
    # A server error is retried twice, then it is an error; a refusal is one at once.
    answers += [Reply(503, None)] * 3 + [Reply(403, None)]
    for _ in range(2):
        with pytest.raises(fb.FeedbackError) as caught:
            api.get("repos/o/r/pulls/7?secret=1")
        assert "t0ken" not in str(caught.value) and "secret=1" not in str(caught.value)
    assert waits == [15.0, 30.0] and answers == []


def _project(tmp_path: Path, text: str = CONFIG) -> list[str]:
    """Write a devloop.toml and return the command line that runs the gate there."""
    (tmp_path / "devloop.toml").write_text(text, encoding="utf-8")
    return ["gate", "--root", str(tmp_path), "--repo", "o/r", "--pr", "7", "--run-url", "u"]


def test_the_command_judges_with_the_rules_of_the_folder_it_runs_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The token, the repository, the number and the default branch's [gate] reach the gate."""
    seen: list[object] = []

    def evaluate(*args: object, outputs: str) -> int:
        """Record what the gate was called with."""
        seen.extend([*args, outputs])
        return 0

    monkeypatch.setattr(reader, "evaluate", evaluate)
    monkeypatch.setenv("GITHUB_TOKEN", "t0ken")
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "out"))
    text = CONFIG + 'context = "May merge"\ncontrol = ["tools/**"]\n'
    assert cli(_project(tmp_path, text)) == 0
    expected = rules.rules(parse_config(text))
    api, out = reader.Api("t0ken", "o/r", "May merge", "u"), str(tmp_path / "out")
    assert seen == [api, "o/r", 7, expected, out]
    assert "tools/**" in expected.control
    # The workflow reads the status's name to report a run that broke.
    assert (tmp_path / "out").read_text() == "context=May merge\n"


def test_a_pull_request_that_cannot_be_read_is_a_failed_run_with_no_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The first read failing leaves exit code 1 and the reason, never a pass."""
    hub = Hub([_file()])
    hub.fail = "/pulls/7"
    monkeypatch.setenv("GITHUB_TOKEN", "t0ken")
    monkeypatch.setattr(reader, "Api", lambda *args: hub)
    assert cli(_project(tmp_path)) == 1
    assert hub.posted == []
    err = capsys.readouterr()
    assert "devloop gate: could not check: cannot read repos/o/r/pulls/7" in err.err
    assert "t0ken" not in err.out + err.err


def test_the_command_cannot_start_without_a_token_a_repository_or_valid_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exit code 2, and nothing is read from GitHub."""
    monkeypatch.setattr(reader, "evaluate", lambda *args: pytest.fail("the gate ran"))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    args = _project(tmp_path)
    assert cli(args) == 2
    assert "GITHUB_TOKEN" in capsys.readouterr().err
    monkeypatch.setenv("GITHUB_TOKEN", "t0ken")
    assert cli([*args[:4], "not a repository", *args[5:]]) == 2
    assert cli([*args[:6], "0"]) == 2
    assert cli(_project(tmp_path, CONFIG + "bogus = 1\n")) == 2
    assert "unknown key gate.bogus" in capsys.readouterr().err
    (tmp_path / "devloop.toml").unlink()
    assert cli(args) == 2


@pytest.mark.parametrize("context", ["Merge\\ngate", "x" * 101])
def test_the_status_name_is_one_short_line(context: str) -> None:
    """It is written to the workflow's outputs and into a log command."""
    with pytest.raises(ConfigError, match=r"gate\.context must be one line"):
        parse_config(f'[framework]\nversion = "0.1"\n[gate]\ncontext = "{context}"\n')


def test_the_head_judged_is_written_to_the_outputs_before_a_later_read_can_fail(
    tmp_path: Path,
) -> None:
    """The workflow reports a run that broke on the head this run judged, not an older one."""
    out = tmp_path / "out"
    for broken in ("/branches-where-head", "/compare/", "/statuses"):
        out.write_text("", encoding="utf-8")
        hub = Hub([CORE], fork=True, **OUTSIDER)
        hub.fail = broken
        assert reader.evaluate(hub, "o/r", 7, GATE, outputs=str(out)) == 1
        assert out.read_text(encoding="utf-8") == f"sha={HEAD}\n"
    # A head that is not a commit id is never written: it would be read as more outputs.
    odd = Hub([_file()], head={"sha": HEAD + "\nposted=true", "repo": {"full_name": "o/r"}})
    out.write_text("", encoding="utf-8")
    with pytest.raises(fb.FeedbackError, match="names no head commit"):
        reader.evaluate(odd, "o/r", 7, GATE, outputs=str(out))
    assert out.read_text(encoding="utf-8") == "" and odd.posted == []


def test_a_workflow_that_expects_another_status_name_does_not_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The workflow reports a broken run under its own name, so the two must agree."""
    monkeypatch.setenv("GITHUB_TOKEN", "t0ken")
    out = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    args = _project(tmp_path)
    monkeypatch.setattr(reader, "evaluate", lambda *args, **more: pytest.fail("the gate ran"))
    assert cli([*args, "--context", "Second gate"]) == 2
    assert capsys.readouterr().err == (
        "devloop gate: the workflow's context is 'Second gate' but gate.context in "
        "devloop.toml is 'Merge gate'; make them the same\n"
    )
    assert not out.exists() or out.read_text(encoding="utf-8") == ""
    # An empty name is a name: a caller that forwards an unset input must not skip the check.
    assert cli([*args, "--context", ""]) == 2
    assert "the workflow's context is ''" in capsys.readouterr().err
    # A name that starts with a dash is read as the name, as the workflow passes it.
    dashed = _project(tmp_path, CONFIG + 'context = "--second"\n')
    assert cli([*dashed, "--context=--other"]) == 2
    assert "is '--other' but gate.context in devloop.toml is '--second'" in capsys.readouterr().err
    _project(tmp_path)
    # The same name, and no name at all (a caller of an older workflow), both start.
    ran: list[str] = []

    def evaluate(*args: object, **more: object) -> int:
        """Record that the gate ran."""
        ran.append("ran")
        return 0

    monkeypatch.setattr(reader, "evaluate", evaluate)
    assert cli([*args, "--context", "Merge gate"]) == 0
    assert cli(args) == 0
    assert ran == ["ran", "ran"]


def test_settings_or_outputs_that_cannot_be_read_or_written_are_start_up_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """One line and exit code 2, as for an invalid devloop.toml: never a traceback."""
    monkeypatch.setattr(reader, "evaluate", lambda *args, **more: pytest.fail("the gate ran"))
    monkeypatch.setenv("GITHUB_TOKEN", "t0ken")
    args = _project(tmp_path)
    # GITHUB_OUTPUT in a folder that does not exist.
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "none" / "out"))
    assert cli(args) == 2
    assert capsys.readouterr().err == "devloop gate: GITHUB_OUTPUT could not be written\n"
    monkeypatch.delenv("GITHUB_OUTPUT")
    # A devloop.toml that is not UTF-8, then one that is a folder.
    (tmp_path / "devloop.toml").write_bytes(b'[framework]\nversion = "\xff\xfe"\n')
    assert cli(args) == 2
    assert capsys.readouterr().err == "devloop gate: devloop.toml could not be read\n"
    (tmp_path / "devloop.toml").unlink()
    (tmp_path / "devloop.toml").mkdir()
    assert cli(args) == 2
    assert capsys.readouterr().err == "devloop gate: devloop.toml could not be read\n"


# The request budget (docs/cost.md, "GitHub API reads per run"). The workflow token has an
# hourly limit and every reply starts a run, so a change that adds a read must show here.

COST = (Path(__file__).resolve().parents[2] / "docs" / "cost.md").read_text(encoding="utf-8")
LANE_READS = [
    "repos/o/r/pulls/7",
    f"repos/o/r/compare/{BASE}...{HEAD}?per_page=1&page=1",
    f"repos/o/r/git/trees/{HEAD}?recursive=1",
    f"repos/o/r/git/trees/{BASE}?recursive=1",
]
STATUSES = f"repos/o/r/commits/{HEAD}/statuses?per_page=100&page=1"
CHECK_RUNS = f"repos/o/r{CHECKS}&per_page=100&page=1"
BUDGET = "the budget is {} (docs/cost.md, 'GitHub API reads per run'); update both if it is needed"


def test_a_light_lane_evaluation_makes_ten_reads() -> None:
    """The light lane reads what the strict lane reads: a check it cannot read is a failure.

    Its verdict does not use the check runs, but the read stays: an unreadable review is
    "could not check" in either lane (test_whatever_cannot_be_read_is_a_failure_never_a_pass).
    """
    hub = Hub([_file()])
    assert hub.run() == 0 and hub.last[0] == "success"
    # Twice: the reviews and the head are read again after the verdict is posted.
    expected = [*LANE_READS, *[STATUSES, CHECK_RUNS, "repos/o/r/pulls/7"] * 2]
    assert hub.asked == expected, f"{len(hub.asked)} reads; " + BUDGET.format(len(expected))
    assert len(hub.posted) == 2
    assert "| `devloop gate`, light lane, one check | 10 | 0 | 2 | 12 |" in COST


def test_a_strict_lane_evaluation_with_two_statuses_and_one_check_makes_ten_reads() -> None:
    """One listing answers every status; each check name is one more read per pass."""
    hub = _reviewed(CORE)
    assert hub.run() == 0 and hub.last[0] == "success"
    expected = [*LANE_READS, *[STATUSES, CHECK_RUNS, "repos/o/r/pulls/7"] * 2]
    assert hub.asked == expected, f"{len(hub.asked)} reads; " + BUDGET.format(len(expected))
    assert len(GATE.statuses) == 2 and len(GATE.checks) == 1 and len(hub.posted) == 2
    assert "| `devloop gate`, strict lane, two statuses and one check | 10 | 0 | 2 | 12 |" in COST

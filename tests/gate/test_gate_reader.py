"""The gate against a fake GitHub: the guards, every failed read, and what is posted.

Invariants (devloop.gate.reader). Nothing is posted for a pull request that is closed,
goes into another branch, or is a fork's on a head a branch here also heads. Pending is
posted once, after those guards. Whatever cannot be read ends as a failure, never a pass.
"""

from __future__ import annotations

from typing import Any

import pytest

from devloop.config import Gate, parse_config
from devloop.gate import reader, rules
from devloop.review import feedback as fb

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


@pytest.mark.parametrize("state", ["closed", "merged", ""])
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


@pytest.mark.parametrize(
    "pull",
    [
        {"head": {"repo": {"full_name": "o/r"}}},
        {"head": None},
        {"base": {"ref": "main", "sha": BASE, "repo": {"full_name": "o/r"}}},
        {"base": "main"},
    ],
)
def test_a_pull_request_with_no_head_or_no_default_branch_is_an_error_with_no_status(
    pull: dict[str, Any],
) -> None:
    """Not "nothing is due": a required status that never arrives must show as a failed run."""
    hub = Hub([_file()], **pull)
    with pytest.raises(fb.FeedbackError, match="no head commit or no default branch"):
        hub.run()
    assert hub.posted == []


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

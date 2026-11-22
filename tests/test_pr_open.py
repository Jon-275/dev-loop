"""Tests for ``devloop pr-open``: refuse a push to a branch whose PR already merged or closed.

The guard checks every ref being pushed (git's stdin), looks PRs up by head branch with
``gh pr list``, and fails open, with a warning, when gh or the network is unavailable.
A fake ``subprocess.run`` stands in for gh, git and ssh.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import pytest

from devloop import pr_open as guard
from devloop.cli import main as devloop_main

ZERO = "0" * 40
PUSHED = "a" * 40
PR_HEAD = "b" * 40
GITHUB = "https://github.com/acme/app.git"


def _pr(
    state: str,
    number: int = 145,
    head: str = PR_HEAD,
    owner: str = "acme",
    name: str = "app",
) -> dict[str, Any]:
    """One ``gh pr list`` row; the head is in ``owner/name`` (default: the pushed repo)."""
    return {
        "state": state,
        "number": number,
        "url": f"https://x/pull/{number}",
        "headRefOid": head,
        "headRepositoryOwner": {"login": owner},
        "headRepository": {"name": name},
    }


@dataclass
class World:
    """A fake ``subprocess.run``: gh's PRs per branch, and the local git and ssh answers."""

    prs: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    gh_returncode: int = 0
    gh_stderr: str = ""
    gh_stdout: str | None = None
    gh_raises: BaseException | None = None
    url: str = GITHUB
    current: str | None = "feat/x"
    local_sha: str | None = PUSHED
    ssh_hosts: dict[str, str] = field(default_factory=dict)
    calls: list[list[str]] = field(default_factory=list)

    def gh_calls(self) -> list[list[str]]:
        """Return the gh commands run."""
        return [c for c in self.calls if c[:1] == ["gh"]]

    def __call__(self, args: Sequence[str], **kwargs: float) -> subprocess.CompletedProcess[str]:
        """Answer one command."""
        argv = list(args)
        self.calls.append(argv)
        assert 0 < kwargs["timeout"] <= 30, "every command is bounded"

        def done(code: int = 0, out: str = "", err: str = "") -> subprocess.CompletedProcess[str]:
            """Return a finished command."""
            return subprocess.CompletedProcess(argv, code, out, err)

        if argv[:1] == ["gh"]:
            if self.gh_raises is not None:
                raise self.gh_raises
            if self.gh_stdout is not None or self.gh_returncode:
                return done(self.gh_returncode, self.gh_stdout or "", self.gh_stderr)
            head = next(a.split("=", 1)[1] for a in argv if a.startswith("--head="))
            return done(0, json.dumps(self.prs.get(head, [])))
        if argv[:3] == ["git", "remote", "get-url"]:
            return (
                done(0, f"https://github.com/{argv[-1]}-owner/app.git\n")
                if argv[-1] != "origin"
                else done(0, self.url + "\n")
            )
        if argv[:2] == ["git", "symbolic-ref"]:
            return done(0, f"{self.current}\n") if self.current else done(1)
        if argv[:2] == ["git", "rev-parse"]:
            return done(0, f"{self.local_sha}\n") if self.local_sha else done(1)
        if argv[:2] == ["ssh", "-G"]:
            return done(0, f"user git\nhostname {self.ssh_hosts.get(argv[-1], argv[-1])}\n")
        raise AssertionError(f"unexpected command {argv}")


def _push(branch: str = "feat/x", sha: str | None = PUSHED) -> guard.Push:
    """One pushed branch."""
    return guard.Push(branch, sha)


def _stdin(*branches: str) -> list[str]:
    """Return git's pre-push stdin for pushing ``branches`` (new on the remote)."""
    return [f"refs/heads/{b} {PUSHED} refs/heads/{b} {ZERO}\n" for b in branches]


class TestVerdict:
    """What gh's answer means for one pushed branch."""

    def test_an_open_pr_allows_the_push(self) -> None:
        """A branch with an open PR may be pushed, even if an older PR finished."""
        world = World(prs={"feat/x": [_pr("OPEN", 9), _pr("MERGED", 8)]})
        assert guard.check_push(_push(), run=world).allowed

    @pytest.mark.parametrize("state", ["MERGED", "CLOSED"])
    def test_continuing_a_merged_or_closed_pr_blocks_the_push(self, state: str) -> None:
        """Commits on top of a finished PR's head are refused, naming the PR."""
        world = World(prs={"feat/x": [_pr(state)]})
        verdict = guard.check_push(_push(), run=world)
        assert not verdict.allowed
        assert state.lower() in verdict.message and "#145" in verdict.message
        assert "new branch" in verdict.message

    def test_a_finished_pr_refuses_whatever_the_commits(self) -> None:
        """Amended, rewritten or unrelated commits are all refused: the name decides.

        An amended merged head (force-pushed) does not contain the PR head, and
        neither does new work under a reused name; both are refused, and the
        message names the override for a deliberate reuse.
        """
        world = World(prs={"feat/x": [_pr("MERGED")]})
        verdict = guard.check_push(_push(), run=world)
        assert not verdict.allowed
        assert guard.OVERRIDE in verdict.message and "reus" in verdict.message
        assert not [c for c in world.calls if c[:2] == ["git", "merge-base"]]

    def test_an_unknown_pushed_commit_is_judged_by_the_pr_alone(self) -> None:
        """By hand, a branch with no local commit is refused when its PR finished."""
        world = World(prs={"feat/x": [_pr("MERGED")]})
        assert not guard.check_push(_push(sha=None), run=world).allowed

    def test_a_branch_with_no_pr_allows_the_push(self) -> None:
        """A new branch with no pull request yet may be pushed, with no warning."""
        verdict = guard.check_push(_push(), run=World())
        assert verdict.allowed and not verdict.warning

    @pytest.mark.parametrize(
        "world",
        [
            World(gh_raises=FileNotFoundError("gh")),
            World(gh_raises=subprocess.TimeoutExpired("gh", 15)),
            World(gh_returncode=1, gh_stderr="error connecting to api.github.com"),
            World(gh_returncode=1, gh_stderr="dial tcp: lookup api.github.com: no such host"),
            World(gh_returncode=4, gh_stderr="To get started with GitHub CLI, run: gh auth login"),
            World(gh_returncode=1, gh_stderr="HTTP 401: Bad credentials"),
        ],
        ids=["no-gh", "timeout", "network", "dns", "not-signed-in", "bad-token"],
    )
    def test_gh_or_network_unavailable_fails_open_with_a_warning(self, world: World) -> None:
        """Only gh missing or signed out, a timeout or no network let the push go ahead."""
        verdict = guard.check_push(_push(), run=world)
        assert verdict.allowed and verdict.warning
        assert "could not" in verdict.message

    @pytest.mark.parametrize(
        "world",
        [
            World(gh_stdout="not json"),
            World(gh_stdout="[null]"),
            World(gh_stdout='[{"number": 1}]'),
            World(gh_returncode=1, gh_stderr="GraphQL: Could not resolve to a Repository"),
            World(gh_raises=PermissionError("gh is not executable")),
        ],
        ids=["garbage", "null-row", "no-state", "unknown-gh-error", "gh-not-executable"],
    )
    def test_any_other_failure_refuses_the_push(self, world: World) -> None:
        """An unreadable answer or an unexpected gh error refuses, naming the override."""
        verdict = guard.check_push(_push(), run=world)
        assert not verdict.allowed
        assert guard.OVERRIDE in verdict.message


class TestLookup:
    """How gh is asked: by head branch, never by number, never as an option."""

    def test_prs_are_listed_by_head_branch_in_the_pushed_repository(self) -> None:
        """``gh pr list --head=<branch> --state=all --repo=<repo>``, one token per value."""
        world = World()
        guard.check_push(_push(), repo="github.com/acme/app", run=world)
        assert world.gh_calls() == [
            [
                "gh",
                "pr",
                "list",
                "--head=feat/x",
                "--state=all",
                "--repo=github.com/acme/app",
                "--json=state,number,url,headRefOid,headRepositoryOwner,headRepository",
            ]
        ]

    def test_an_open_fork_pr_with_the_same_branch_name_does_not_unblock(self) -> None:
        """Only PRs whose head is in the pushed repository count.

        A fork's open PR named like a merged same-repo PR's branch must not hit
        the open-PR exception: the push to the merged branch is refused.
        """
        world = World(
            prs={
                "feat/x": [
                    _pr("OPEN", 300, owner="someone-else", name="app"),
                    _pr("MERGED", 145),
                ]
            }
        )
        repo = "github.com/acme/app"
        verdict = guard.check_push(_push(), repo=repo, run=world)
        assert not verdict.allowed and "#145" in verdict.message

    def test_head_repository_matching_ignores_case(self) -> None:
        """GitHub owner and repository names are case-insensitive."""
        world = World(prs={"feat/x": [_pr("OPEN", owner="ACME", name="App")]})
        assert guard.check_push(_push(), repo="github.com/acme/app", run=world).allowed

    def test_only_fork_prs_mean_no_pr_of_this_repository(self) -> None:
        """A branch whose only finished PR comes from a fork is not refused."""
        world = World(prs={"feat/x": [_pr("MERGED", owner="someone-else")]})
        assert guard.check_push(_push(), repo="github.com/acme/app", run=world).allowed

    def test_a_numeric_branch_is_a_branch_not_a_pr_number(self) -> None:
        """Branch ``87`` is looked up as a head branch, so PR #87 is never consulted."""
        world = World(prs={"87": []})
        assert guard.check_push(_push("87"), run=world).allowed
        (call,) = world.gh_calls()
        assert "--head=87" in call and "87" not in call

    def test_a_branch_named_like_an_option_cannot_inject_one(self) -> None:
        """``--web`` reaches gh only inside ``--head=--web``."""
        world = World()
        guard.check_push(_push("--web"), run=world)
        (call,) = world.gh_calls()
        assert "--head=--web" in call and "--web" not in call


class TestRepository:
    """The PR is looked up in the repository being pushed to, not gh's default."""

    @pytest.mark.parametrize(
        ("url", "repo"),
        [
            (GITHUB, "github.com/acme/app"),
            ("https://github.com/acme/app", "github.com/acme/app"),
            ("https://x-access-token:ghs_SECRET@github.com/a/b.git", "github.com/a/b"),
            ("git@github.com:acme/app.git", "github.com/acme/app"),
            ("ssh://git@github.com/a/b.git", "github.com/a/b"),
            ("ssh://git@ghe.example.com:2222/org/repo.git", "ghe.example.com/org/repo"),
        ],
    )
    def test_a_remote_url_names_its_repository(self, url: str, repo: str) -> None:
        """HTTPS, scp-style and ssh:// remotes all name host/owner/repo."""
        assert guard.repo_from_url(url) == repo

    @pytest.mark.parametrize("url", ["", "/srv/git/repo.git", "file:///srv/git/repo", "origin"])
    def test_a_url_with_no_owner_and_repo_names_none(self, url: str) -> None:
        """A local path or bare name is not a GitHub repository."""
        assert guard.repo_from_url(url) is None

    def test_an_ssh_host_alias_is_resolved(self) -> None:
        """``git@github-work:o/r`` (an ssh config alias) is looked up on the real host."""
        world = World(ssh_hosts={"github-work": "github.com"})
        repo = guard.repo_from_url("git@github-work:acme/app.git", run=world)
        assert repo == "github.com/acme/app"
        (call,) = [c for c in world.calls if c[:1] == ["ssh"]]
        assert call == ["ssh", "-G", "--", "github-work"]

    def test_an_option_like_ssh_host_is_never_passed_to_ssh(self) -> None:
        """A host starting with ``-`` is not resolved, so it cannot become an ssh option."""
        world = World()
        guard.repo_from_url("ssh://git@-Fevil/o/r.git", run=world)
        guard.repo_from_url("ssh://git@-oProxyCommand=x/o/r.git", run=world)
        assert not [c for c in world.calls if c[:1] == ["ssh"]]

    def test_the_push_url_wins_over_gh_default_repository(self) -> None:
        """As a hook, the push URL git passes picks the repository gh queries."""
        world = World(prs={"feat/x": [_pr("MERGED", owner="other-owner")]})
        url = "git@github.com:other-owner/app.git"
        code = guard.main(
            ["--git-pre-push", "origin", url], env={}, run=world, stdin=_stdin("feat/x")
        )
        assert code == guard.REFUSED
        (call,) = world.gh_calls()
        assert "--repo=github.com/other-owner/app" in call

    def test_by_hand_the_named_remote_push_url_is_used(self) -> None:
        """``--remote`` (default origin) names the repository by its push URL."""
        world = World()
        assert guard.main(["feat/x"], env={}, run=world) == 0
        assert guard.main(["--remote", "upstream", "feat/x"], env={}, run=world) == 0
        gets = [c for c in world.calls if c[:3] == ["git", "remote", "get-url"]]
        assert all("--push" in c for c in gets)
        repos = [a for c in world.gh_calls() for a in c if a.startswith("--repo=")]
        assert repos == [
            "--repo=github.com/acme/app",
            "--repo=github.com/upstream-owner/app",
        ]


class TestRedaction:
    """No credential in a remote URL is ever printed."""

    @pytest.mark.parametrize(
        ("url", "shown"),
        [
            (
                "https://oauth2:glpat-SECRET123@gitlab.com/grp/sub/repo.git",
                "https://gitlab.com/grp/sub/repo.git",
            ),
            ("https://token@example.com/a.git", "https://example.com/a.git"),
            ("ssh://deploy:pw@host.example/x/y/z.git", "ssh://host.example/x/y/z.git"),
            ("git@gitlab.com:grp/sub/repo.git", "gitlab.com:grp/sub/repo.git"),
            ("tok:SECRET9@gitlab.com:grp/sub/repo.git", "gitlab.com:grp/sub/repo.git"),
            ("https://u:p@ss@gitlab.com/grp/repo.git", "https://gitlab.com/grp/repo.git"),
            ("/srv/git/mirror.git", "/srv/git/mirror.git"),
        ],
    )
    def test_userinfo_is_dropped(self, url: str, shown: str) -> None:
        """User names, passwords and tokens are removed."""
        assert guard.redact_url(url) == shown

    def test_a_non_github_url_with_a_token_is_not_printed(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The fail-open warning names the remote without its credentials."""
        url = "https://oauth2:glpat-SECRET123@gitlab.com/grp/sub/repo.git"
        code = guard.main(
            ["--git-pre-push", "origin", url], env={}, run=World(), stdin=_stdin("feat/x")
        )
        err = capsys.readouterr().err
        assert code == 0 and "warning" in err
        assert "SECRET" not in err and "oauth2" not in err
        assert "gitlab.com/grp/sub/repo.git" in err


class TestRefs:
    """Which pushed refs are checked."""

    def test_only_branch_updates_are_checked(self) -> None:
        """Tags and branch deletions are not checked; branch updates are."""
        assert guard.branch_of("refs/heads/feat/x", PUSHED) == "feat/x"
        assert guard.branch_of("refs/tags/v1", PUSHED) is None
        assert guard.branch_of("refs/heads/feat/x", ZERO) is None
        assert guard.branch_of("", PUSHED) is None

    def test_protected_branches_are_not_checked(self) -> None:
        """Main has no PR of its own; branch protection guards it."""
        assert guard.branch_of("refs/heads/main", PUSHED) is None

    def test_every_ref_on_stdin_is_read(self) -> None:
        """All pushed branches are read, in order; tags and deletions are skipped."""
        lines = [
            *_stdin("ok-b2", "merged-b2"),
            f"refs/tags/v1 {PUSHED} refs/tags/v1 {ZERO}\n",
            f"(delete) {ZERO} refs/heads/gone {PUSHED}\n",
        ]
        pushes = guard.pushes_from_stdin(lines)
        assert [p.branch for p in pushes] == ["ok-b2", "merged-b2"]
        assert {p.sha for p in pushes} == {PUSHED}


class TestMain:
    """The hook and by-hand entry points."""

    def test_a_later_ref_in_a_multi_ref_push_is_refused(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The finished PR's branch is caught even when it is not the first ref."""
        world = World(prs={"merged-b2": [_pr("MERGED", 7)]})
        code = guard.main(
            ["--git-pre-push", "origin", GITHUB],
            env={},
            run=world,
            stdin=_stdin("ok-b2", "merged-b2"),
        )
        assert code == guard.REFUSED
        assert [c[3] for c in world.gh_calls()] == ["--head=ok-b2", "--head=merged-b2"]
        assert "'merged-b2' (#7" in capsys.readouterr().err

    def test_an_explicit_branch_argument_is_checked(self) -> None:
        """``devloop pr-open <branch>`` checks that branch; refusal is exit 1."""
        assert guard.main(["feat/x"], env={}, run=World(prs={"feat/x": [_pr("OPEN")]})) == 0
        assert guard.main(["feat/x"], env={}, run=World(prs={"feat/x": [_pr("CLOSED")]})) == 1

    def test_the_override_lets_a_deliberate_push_through(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """ALLOW_PUSH_TO_FINISHED_PR=1 allows the push, with a warning."""
        env = {"ALLOW_PUSH_TO_FINISHED_PR": "1"}
        assert guard.main(["feat/x"], env=env, run=World(prs={"feat/x": [_pr("MERGED")]})) == 0
        assert "ALLOW_PUSH_TO_FINISHED_PR" in capsys.readouterr().err

    def test_fail_open_exits_zero_and_warns(self, capsys: pytest.CaptureFixture[str]) -> None:
        """No gh: exit 0 with a warning on stderr."""
        assert guard.main(["feat/x"], env={}, run=World(gh_raises=FileNotFoundError("gh"))) == 0
        assert "warning" in capsys.readouterr().err.lower()

    def test_a_detached_head_says_nothing_was_checked(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """By hand on a detached HEAD the guard says so instead of passing silently."""
        assert guard.main([], env={}, run=World(current=None)) == 0
        assert "detached" in capsys.readouterr().err

    def test_a_bug_in_the_guard_refuses_the_push(self, capsys: pytest.CaptureFixture[str]) -> None:
        """An unexpected error refuses the push (it is not gh being unavailable)."""

        def broken(args: Sequence[str], **kwargs: float) -> subprocess.CompletedProcess[str]:
            """Fail every command unexpectedly."""
            raise RuntimeError("boom")

        code = guard.main(
            ["--git-pre-push", "origin", GITHUB], env={}, run=broken, stdin=_stdin("x")
        )
        err = capsys.readouterr().err
        assert code == guard.REFUSED
        assert "boom" in err and guard.OVERRIDE in err

    def test_the_override_lets_a_push_past_a_bug(self, capsys: pytest.CaptureFixture[str]) -> None:
        """ALLOW_PUSH_TO_FINISHED_PR=1 also gets a push past a broken guard, with a warning."""

        def broken(args: Sequence[str], **kwargs: float) -> subprocess.CompletedProcess[str]:
            """Fail every command unexpectedly."""
            raise RuntimeError("boom")

        env = {guard.OVERRIDE: "1"}
        code = guard.main(
            ["--git-pre-push", "origin", GITHUB], env=env, run=broken, stdin=_stdin("x")
        )
        assert code == 0 and "warning" in capsys.readouterr().err


def test_the_devloop_command_runs_the_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """``devloop pr-open`` hands its arguments to the guard unchanged."""
    seen: list[list[str]] = []

    def fake(argv: Sequence[str]) -> int:
        """Record the arguments."""
        seen.append(list(argv))
        return 0

    monkeypatch.setattr(guard, "main", fake)
    assert devloop_main(["pr-open", "--remote", "upstream", "feat/x"]) == 0
    assert seen == [["--remote", "upstream", "feat/x"]]

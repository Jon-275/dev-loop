"""``devloop pr-open``: refuse a push to a branch whose pull request already merged or closed.

Auto-merge can merge a pull request while review fixes are still being pushed; commits
pushed after that land on a finished branch and never reach ``main``. Before a push this
asks GitHub about the branch's pull requests (``gh pr list --head``, never by number, in
the repository being pushed to) and refuses the push when the branch has a merged or
closed PR there and no open one, whatever the commits. Open a new branch from
``origin/main`` and carry the commits there instead. Reusing a finished PR's branch name
for new work needs ``ALLOW_PUSH_TO_FINISHED_PR=1``.

As git's ``pre-push`` hook (``--git-pre-push REMOTE URL``) it checks every ref git
passes on stdin. By hand (``devloop pr-open [--remote NAME] [branch ...]``) it checks
the named branches, or the current one.

It fails open, with a warning, only when it cannot ask GitHub: ``gh`` missing or signed
out, a timeout, or no network. A remote that is not a GitHub repository is not checked
(a warning). Any other failure refuses the push.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: Seconds to wait for ``gh``; past this the guard fails open.
GH_TIMEOUT = 15.0
#: Seconds to wait for ``git`` and ``ssh`` lookups.
LOCAL_TIMEOUT = 5.0
#: Branches without a PR of their own; branch protection guards them.
PROTECTED = frozenset({"main", "master"})
OVERRIDE = "ALLOW_PUSH_TO_FINISHED_PR"
#: Exit status of ``--git-pre-push`` when it refuses the push.
REFUSED = 3
_ZERO_SHA = "0" * 40
_FINISHED = {"MERGED": "merged", "CLOSED": "closed"}
#: gh errors that mean GitHub could not be asked (signed out, no network).
_UNAVAILABLE = (
    "gh auth login",
    "not logged in",
    "authentication required",
    "bad credentials",
    "http 401",
    "error connecting to",
    "no such host",
    "dial tcp",
    "could not resolve host",
    "connection refused",
    "connection reset",
    "network is unreachable",
    "tls handshake timeout",
    "i/o timeout",
    "timed out",
)

Runner = Callable[..., "subprocess.CompletedProcess[str]"]

# URL forms (https, ssh, git) and scp-style ``user@host:owner/repo.git``. The
# userinfo part is greedy up to the last ``@``, as a password may contain one.
_URL = re.compile(
    r"^(?P<scheme>[a-z][a-z0-9+.-]*)://(?:[^/]*@)?(?P<host>[^:/@]+)(?::\d+)?/(?P<path>.+)$"
)
_SCP = re.compile(r"^(?:[^/]*@)?(?P<host>[^:/@]+):(?P<path>[^/].*)$")
_USERINFO = re.compile(r"^(?P<scheme>[a-z][a-z0-9+.-]*://)[^/]*@")


def redact_url(url: str) -> str:
    """``url`` without any user name, password or token, safe to print."""
    url = url.strip()
    if "://" in url:
        return _USERINFO.sub(lambda m: m["scheme"], url)
    _, at, rest = url.rpartition("@")
    if at and re.match(r"^[^:/@]+:", rest):
        return rest
    return url


def _ssh_hostname(alias: str, run: Runner) -> str:
    """Return the host an ssh alias (``Host github-work`` in ssh config) connects to."""
    if alias.startswith("-"):
        return alias  # never an ssh option
    try:
        done = run(
            ["ssh", "-G", "--", alias],
            capture_output=True,
            text=True,
            timeout=LOCAL_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return alias
    for line in (done.stdout or "").splitlines() if done.returncode == 0 else []:
        key, _, value = line.partition(" ")
        if key.lower() == "hostname" and value.strip():
            return value.strip()
    return alias


def repo_from_url(url: str, *, run: Runner | None = None) -> str | None:
    """``host/owner/repo`` for a remote URL, or None when it names no such repository.

    Local paths, ``file://`` URLs and bare remote names give None. With ``run``,
    an ssh host alias is resolved to the host it connects to.
    """
    url = url.strip()
    if not url or url.startswith(("/", ".", "file:")):
        return None
    match = _URL.match(url)
    ssh = match is not None and match["scheme"] in ("ssh", "git+ssh", "ssh+git")
    if match is None and "://" not in url:
        match, ssh = _SCP.match(url), True
    if match is None:
        return None
    parts = match["path"].strip("/").removesuffix(".git").split("/")
    if len(parts) != 2 or not all(parts):
        return None
    host = match["host"]
    if ssh and run is not None and host != "github.com":
        host = _ssh_hostname(host, run)
    return f"{host}/{parts[0]}/{parts[1]}"


@dataclass(frozen=True)
class Verdict:
    """Whether a push may go ahead, and what to tell the person pushing."""

    allowed: bool
    message: str = ""
    warning: bool = False


@dataclass(frozen=True)
class Push:
    """One branch update in a push: the branch and the commit it will point to."""

    branch: str
    #: The commit being pushed; None when it is not known (checked by hand).
    sha: str | None


def branch_of(remote_ref: str, to_ref: str) -> str | None:
    """Return the branch a pushed ref updates, or None when it is not checked.

    Tags, branch deletions (``to_ref`` all zeros) and protected branches are
    not checked.
    """
    prefix = "refs/heads/"
    if not remote_ref.startswith(prefix) or to_ref == _ZERO_SHA:
        return None
    branch = remote_ref[len(prefix) :]
    return None if not branch or branch in PROTECTED else branch


def pushes_from_stdin(lines: Iterable[str]) -> list[Push]:
    """Every checked branch update in git's pre-push stdin.

    Each line is ``<local ref> <local sha> <remote ref> <remote sha>``.
    """
    pushes = []
    for line in lines:
        parts = line.split()
        if len(parts) != 4:
            continue
        _, local_sha, remote_ref, _ = parts
        branch = branch_of(remote_ref, local_sha)
        if branch is not None:
            pushes.append(Push(branch, local_sha))
    return pushes


def _fail_open(branch: str, reason: str) -> Verdict:
    """Allow the push with a warning that GitHub could not be asked."""
    return Verdict(
        allowed=True,
        warning=True,
        message=(
            f"warning: could not check the pull request for {branch!r} ({reason}); "
            "pushing anyway. Make sure its PR has not already merged or closed."
        ),
    )


def _broken(branch: str, reason: str) -> Verdict:
    """Refuse the push: the check went wrong in a way that is not GitHub being unavailable."""
    return Verdict(
        allowed=False,
        message=(
            f"error: the pre-push guard could not check {branch!r} ({reason}); "
            f"push refused. To push anyway, set {OVERRIDE}=1."
        ),
    )


def _refusal(branch: str, pr: Mapping[str, Any]) -> Verdict:
    """Return the refusal for a push to the branch of the finished ``pr``."""
    state = _FINISHED[str(pr.get("state", "")).upper()]
    return Verdict(
        allowed=False,
        message=(
            f"error: the pull request for {branch!r} (#{pr.get('number')} "
            f"{pr.get('url') or ''}) has already {state}. Commits pushed here will "
            "never reach main. Create a new branch from origin/main, carry the commits "
            "there and open a new PR. To push anyway (for example, reusing the branch "
            f"name for new work), set {OVERRIDE}=1."
        ),
    )


def _head_in(pr: Mapping[str, Any], repo: str | None) -> bool:
    """Whether ``pr``'s head branch is in ``repo`` (``host/owner/repo``).

    ``gh pr list --head`` matches the branch name in every repository, so a
    fork's PR from a same-named branch is listed too; only PRs whose head is
    in the repository being pushed to count. When gh does not say where the
    head is, or ``repo`` is not known, the PR counts.
    """
    if repo is None:
        return True
    owner = (pr.get("headRepositoryOwner") or {}).get("login")
    name = (pr.get("headRepository") or {}).get("name")
    if not owner or not name:
        return True
    _, want_owner, want_name = repo.split("/")
    return bool(str(owner).lower() == want_owner.lower() and str(name).lower() == want_name.lower())


def check_push(
    push: Push | str, *, repo: str | None = None, run: Runner = subprocess.run
) -> Verdict:
    """Whether ``push`` may go ahead, from ``gh pr list --head <branch> --state all``.

    PRs are found by head branch name (never read as a PR number or option),
    keeping only those whose head is in ``repo`` (a fork's PR from a
    same-named branch never counts). ``repo`` (``host/owner/repo``) is the
    repository being pushed to; None leaves the choice to gh's default
    repository. An open PR allows the push. Otherwise any merged or closed PR
    refuses it, whatever the commits being pushed.
    """
    if isinstance(push, str):
        push = Push(push, None)
    branch = push.branch
    args = ["gh", "pr", "list", f"--head={branch}", "--state=all"]
    if repo is not None:
        args.append(f"--repo={repo}")
    args.append("--json=state,number,url,headRefOid,headRepositoryOwner,headRepository")
    try:
        done = run(args, capture_output=True, text=True, timeout=GH_TIMEOUT, check=False)
    except FileNotFoundError:
        return _fail_open(branch, "gh is not installed")
    except subprocess.TimeoutExpired:
        return _fail_open(branch, f"gh did not answer within {GH_TIMEOUT:g}s")
    except OSError as error:
        return _broken(branch, f"gh could not run: {error}")
    if done.returncode != 0:
        detail = (done.stderr or "").strip().splitlines()
        reason = detail[0] if detail else f"gh exited {done.returncode}"
        if any(sign in (done.stderr or "").lower() for sign in _UNAVAILABLE):
            return _fail_open(branch, reason)
        return _broken(branch, reason)
    try:
        answer: Any = json.loads(done.stdout)
        prs = [dict(pr) for pr in answer if _head_in(dict(pr), repo)]
        states = [str(pr["state"]).upper() for pr in prs]
    except (ValueError, TypeError, KeyError, AttributeError):
        return _broken(branch, "gh gave an unreadable answer")
    if "OPEN" in states:
        return Verdict(allowed=True)
    for pr in prs:  # newest first, as gh lists them
        if str(pr["state"]).upper() in _FINISHED:
            return _refusal(branch, pr)
    return Verdict(allowed=True)


def _git_output(args: Sequence[str], run: Runner) -> str | None:
    """Return the stripped output of a git command, or None when it fails."""
    try:
        done = run(list(args), capture_output=True, text=True, timeout=LOCAL_TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = (done.stdout or "").strip()
    return out if done.returncode == 0 and out else None


def _report(
    pushes: Sequence[Push],
    url: str,
    source: str,
    env: Mapping[str, str],
    run: Runner,
) -> bool:
    """Check every push to ``url``; True when any is refused. Messages go to stderr."""
    repo = repo_from_url(url, run=run)
    refused = False
    for push in pushes:
        if repo is None:
            reason = f"{source} {redact_url(url)!r} is not a GitHub repository"
            print(_fail_open(push.branch, reason).message, file=sys.stderr)
            continue
        verdict = check_push(push, repo=repo, run=run)
        if verdict.allowed:
            if verdict.message:
                print(verdict.message, file=sys.stderr)
        elif env.get(OVERRIDE) == "1":
            print(f"warning: {OVERRIDE}=1; {verdict.message}", file=sys.stderr)
        else:
            print(verdict.message, file=sys.stderr)
            refused = True
    return refused


def _parser() -> argparse.ArgumentParser:
    """Build the command line."""
    parser = argparse.ArgumentParser(
        prog="devloop pr-open", description="Refuse a push to a merged or closed PR's branch."
    )
    parser.add_argument("branches", nargs="*", help="branches to check (default: current)")
    parser.add_argument("--remote", default="origin", help="remote pushed to (default: origin)")
    parser.add_argument(
        "--git-pre-push",
        nargs=2,
        metavar=("REMOTE", "URL"),
        help="run as git's pre-push hook: the refs come on stdin",
    )
    return parser


def _check(
    args: argparse.Namespace, env: Mapping[str, str], run: Runner, stdin: Iterable[str] | None
) -> int:
    """Run the check the command line asks for; its exit status."""
    if args.git_pre_push is not None:
        remote, url = args.git_pre_push
        pushes = pushes_from_stdin(sys.stdin if stdin is None else stdin)
        return REFUSED if _report(pushes, url, f"remote {remote!r} URL", env, run) else 0
    branches = [b for b in args.branches if b and b not in PROTECTED]
    if not args.branches:
        current = _git_output(["git", "symbolic-ref", "--quiet", "--short", "HEAD"], run)
        if current is None:
            print(
                "nothing checked: HEAD is detached; name the branch you will push", file=sys.stderr
            )
            return 0
        branches = [current] if current not in PROTECTED else []
    if not branches:
        return 0
    pushes = [
        Push(b, _git_output(["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{b}"], run))
        for b in branches
    ]
    url = _git_output(["git", "remote", "get-url", "--push", args.remote], run) or ""
    return 1 if _report(pushes, url, f"remote {args.remote!r} URL", env, run) else 0


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    run: Runner = subprocess.run,
    stdin: Iterable[str] | None = None,
) -> int:
    """Check the push; non-zero when it is refused.

    By hand a refusal is 1; as git's hook (``--git-pre-push``) it is
    :data:`REFUSED`. A bug in the guard refuses too, unless the override is set.
    """
    args = _parser().parse_args(list(sys.argv[2:] if argv is None else argv))
    environ = os.environ if env is None else env
    try:
        return _check(args, environ, run, stdin)
    except Exception as error:  # noqa: BLE001 - a bug is not gh being unavailable: refuse
        if environ.get(OVERRIDE) == "1":
            print(f"warning: {OVERRIDE}=1; the pre-push guard failed ({error})", file=sys.stderr)
            return 0
        print(
            f"error: the pre-push guard failed ({error}); push refused. "
            f"To push anyway, set {OVERRIDE}=1.",
            file=sys.stderr,
        )
        return REFUSED if args.git_pre_push is not None else 1

# Writing a status check

A required commit status decides whether a pull request may merge. A workflow that posts
one can block every merge when it breaks, or let through a change nobody judged. These
are the rules devloop's own status workflows follow, in the order you meet them when you
write one. Each rule names the failure it prevents and the place in this repository that
shows it done.

Links to code go to the `main` branch:
[`labels.yml`][labels], [`review.yml`][review], [`gate.yml`][gate],
[`status_guard.py`][guard] and [`gate/reader.py`][reader].

## 1. A status belongs to a commit, so guard before any write

GitHub stores a status on a commit, not on a pull request. Two pull requests with the
same head commit share its statuses. Before the first write, check three things:

- the pull request is open;
- it goes into the branch you mean to protect;
- it is not a fork's pull request whose head commit is also the head of a branch in the
  repository.

**What goes wrong without it.** Anyone can point a fork's branch at the head of one of
your branches and open a pull request. That pull request has no review threads, so a
labels check passes it, and the pass lands on your commit over the failure it had earned.
A verdict for a closed pull request, or for one into a release branch, lands on the same
commit in the same way.

See [a status belongs to a commit](security.md#a-status-belongs-to-a-commit) and the
invariants at the top of [`status_guard.py`][guard] and [`gate/reader.py`][reader].

## 2. One writer of pending, and only after the guards

Decide which piece of code posts `pending`, and let nothing else post it. Post it after
the guards of rule 1 have passed, never before.

**What goes wrong without it.** A shell step that posts `pending` "to be safe" at the top
of the job writes on the shared commit before anything has checked that a status is due.
Two writers also race: the shell's `pending` can land after the program's verdict and
leave a finished check looking unfinished for ever.

In [`gate.yml`][gate] and [`labels.yml`][labels] only devloop writes `pending` or a
verdict. The shell posts one thing, a `failure` for a run that broke, which can never
let a pull request pass.

## 3. "Could not check" is a failure

When the check cannot judge, it posts `failure` and the job fails. That covers a missing
token, an error from GitHub, a timeout, and a reply in the wrong shape. Three ways to get
this wrong:

- **A pass.** The check reports "healthy" for a change nobody judged.
- **A stale pass left standing.** The run dies before it posts, and the `success` from an
  earlier run stays on the commit. Add a last step that runs on failure and posts
  `failure` on the head.
- **A silent exit 0.** The job is green, no status is written, and the required status
  waits for ever with nothing to look at.

A malformed reply is "could not check", not "nothing to do". A pull request record with
no `state` is not a closed pull request. A list of branches that is not a list is not an
empty list. Example: `gh api ... --jq length` prints nothing when the call fails, and a
shell test that treats an empty string as zero then reads "no branch shares this head".

See [decision 0003](decisions/0003-fail-closed.md), and the last step of
[`gate.yml`][gate] and [`labels.yml`][labels].

## 4. One run at a time for each pull request, and never cancel one

A check reads GitHub, then posts. Two runs at once can finish out of order, and the one
that read first posts last: an old answer over a new one. Put the job in a concurrency
group keyed by the pull request, with `cancel-in-progress: false`.

**What goes wrong with cancelling.** A cancelled run leaves no answer. If it had already
posted `pending`, the commit stays pending. It also leaves a row of cancelled jobs on
every commit, and people stop looking at red marks.

[`gate.yml`][gate] uses the group. That is what prevents the old answer.

Reading again after posting is not a substitute. [`labels.yml`][labels] has no group,
because every reply starts a run, and each run reads the threads again after it posts.
That only lowers the chance: another run can still finish between a run's last read and
its post, and the older answer then lands last. One run at a time for each pull request
is the only thing that rules it out.

## 5. Judge the head as it is now

Read the head commit from the API when the run starts work. Do not take it from the
event. A run that waited its turn in a concurrency group may start after a push, and the
event still names the old head.

**What goes wrong without it.** The run judges a commit that is no longer the head, and
the new head gets no status. Or the run breaks and reports its failure on the old commit,
where nobody sees it.

Report on the commit that was judged. Have the program write the commit it read to the
step's outputs as soon as it knows it, and have the failure step use that, falling back
to the head the first step found. See the "Find the head commit" and "Report a run that
could not check" steps of [`gate.yml`][gate].

## 6. Trusted code from the default branch, pinned; the pull request is data

A job whose token can write a status checks out the default branch and installs the tool
from a pinned release. It reads the pull request through the API or as git objects. It
never installs, imports or runs anything from it, and shares no cache with it.

**What goes wrong without it.** A pull request edits the check, or a script the check
calls, and passes itself. An action pinned by tag, or a tool installed from a branch, is
changed under you by someone else.

See [trusted reviews](security.md#trusted-reviews-on-pull_request_target). Every action
in the shared workflows is pinned by commit, and uv and Python by version.

## 7. Decide from the changed files, read by commit

When the verdict depends on what the pull request changes, read the changed files by the
base and head commits. Sort them with an allow-list for "documents" and a deny-list of
control files that are never documents. Take both lists from the default branch.

**What goes wrong without it.**

- A check that reads the description passes any pull request that writes "docs only".
- A check that reads the pull request's own copy of the settings passes a pull request
  that adds `**` to the list of documents.
- A list read by pull request number can change between the read and the post. A list
  read by two commits is the list for the commit you post on.
- With an allow-list alone, `docs/conftest.py` is a document. With a deny-list alone,
  every file type nobody thought of is one.

A list that cannot be read whole counts as a control change. See
[two lanes](gate.md#two-lanes), [control files](gate.md#control-files) and
[a pull request does not choose its own rules](security.md#a-pull-request-does-not-choose-its-own-rules).

## 8. Count only signals from the account or app that should post them

A status counts when the expected account posted it. A check run counts under its name
and from the expected app's id. A reply counts from an owner, member or collaborator.

**What goes wrong without it.** Any workflow on a branch of the repository can post a
status of any name. Any app can create a check run called `Security scan`. Any account
can reply `accepted:` to a finding.

See [what the gate trusts](gate.md#what-it-trusts) and
[statuses any workflow can post](security.md#statuses-any-workflow-can-post).

## 9. Pin the whole job in a test

Write a test that compares the whole job with an expected value: every step, every `run`
line, the `env`, the permissions and the concurrency group.

**What goes wrong with pinning single keys.** A test that asserts
`permissions.statuses == "write"` and "no step posts `state=success`" still passes when
someone adds `|| true` to the install line, or a new step that posts under another
spelling. An added shell line is a behaviour change, and only a whole-job comparison
makes it fail a test and reach a reviewer.

See [`tests/test_gate_workflow.py`][gate-test] and
[`tests/test_status_guard_workflows.py`][guard-test]. The second also runs the shell
guard against a fake `gh`, including the reply that is not a number.

## 10. Introduce a required status in three steps

1. **Report it.** Merge the workflow. The status appears on every pull request and
   nothing requires it.
2. **Compare it.** On real pull requests, check it against what it replaces: one before
   and after its reviews pass, one of documents only, one from a fork, one whose branch
   was updated from the base.
3. **Require it.** Only then add it to branch protection and remove what it replaces.

**What goes wrong without it.** A status required on the day it ships blocks every open
pull request the first time it is wrong, and the pull request that fixes it is blocked
too.

See [rolling it out](gate.md#rolling-it-out).

## 11. Three facts about events and calls

- **`pull_request_target` runs the base branch's workflow.** A new trusted workflow does
  not run on the pull request that adds it, so it cannot report there. It first reports
  on the next pull request. Do not require its status until then (rule 10).
- **A review or reply event on a fork's pull request carries a read-only token.** A job
  started by `pull_request_review` or `pull_request_review_comment` cannot write a status
  there. Skip the status job for forks on those events and let the next push or a manual
  run post it. Otherwise every reply from an outside author is a red job.

See [when the gate runs](gate.md#when-it-runs).
- **A call to a shared workflow in another repository must grant its default
  permissions.** If the called workflow's top-level `permissions` ask for more than the
  calling job grants, GitHub does not start the calling workflow at all, and every job
  in it is lost, not only the one call. Because of the first fact, the pull request that
  introduces the call does not show it. Before merging a new call, push a throwaway
  branch with a workflow that makes the same call with `if: false`: a run that is
  skipped has started; a start-up failure has not.


## 12. Check a gate's exit code, not its filtered output

Run each gate so its own exit code decides. A pipeline's exit code is its last
command's:

```bash
pytest -q | tail -3      # exit code 0: tail succeeded, whatever pytest did
```

In a workflow, run the gate as its own step, or set `set -o pipefail`. By hand, run it
unfiltered, or print the code:

```bash
pytest -q; echo "exit=$?"
```

**What goes wrong without it.** A failing gate reads as a pass. The same holds for
`grep`: `ruff check . | grep -c error` exits 1 when there are no errors and 0 when there
are some.

[labels]: https://github.com/Jon-275/dev-loop/blob/main/.github/workflows/labels.yml
[review]: https://github.com/Jon-275/dev-loop/blob/main/.github/workflows/review.yml
[gate]: https://github.com/Jon-275/dev-loop/blob/main/.github/workflows/gate.yml
[guard]: https://github.com/Jon-275/dev-loop/blob/main/src/devloop/review/status_guard.py
[reader]: https://github.com/Jon-275/dev-loop/blob/main/src/devloop/gate/reader.py
[gate-test]: https://github.com/Jon-275/dev-loop/blob/main/tests/test_gate_workflow.py
[guard-test]: https://github.com/Jon-275/dev-loop/blob/main/tests/test_status_guard_workflows.py

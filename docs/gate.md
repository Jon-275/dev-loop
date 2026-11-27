# Merge gate

The merge gate is one commit status, `Merge gate` by default. It answers one question:
may this pull request merge, as far as the reviews go?

Without it, branch protection requires one status per reviewer. A required status that
never arrives blocks for ever, so a pull request that needs no review still waits for
every reviewer. One status can say "documents only: no review needed".

`devloop gate` computes it, and the shared workflow `gate.yml` runs it. The settings are
the [`[gate]` table](configuration.md#gate) of `devloop.toml`.

## Two lanes

A pull request is in the **light lane** when every changed file is a document:

- the path matches a `gate.light` glob, in exactly that spelling. By default these are
  Markdown and images under `docs/`, a `.md` file directly under the changelog folder,
  and `README.md`, `CONTRIBUTING.md`, `SUPPORT.md` and `CODE_OF_CONDUCT.md` at the root;
- the path is plain: ASCII letters, digits, `.`, `_`, `-` and `/`, at most 200
  characters, with no part that starts with `.` or `-`;
- the file is a regular file that cannot be run (git mode `100644`), unless it was removed;
- a renamed file is a document under both names;
- no path differs only by case from another changed path or from a path on the default
  branch;
- the pull request changes at most `gate.max_light_files` files (100 by default).

The light lane waits for no reviewer.

So light-lane files merge without a model review. Markdown and SVG can hold raw HTML or
script. If the project publishes its docs, read such changes from outside authors as you
would read code.

Everything else is in the **strict lane**. A strict pull request needs every status in
`gate.statuses` and every check in `gate.checks` to have passed on its head commit.

## Control files

A control file defines, configures or is run by a check, a reviewer or an agent. A
control file is never a document. These are built in, compared without regard to case:

- any hidden file or folder, such as `.github/`, `.claude/` or `.gitattributes`;
- `devloop.toml` and the `devloop/` folder;
- the folders and files `devloop.toml` names: the project's skills, the folders skills
  are rendered to, the lenses, the council roles, the lessons seed and page, the file
  the lessons' rules go in, and the review's feedback and patterns files;
- agent instructions in any folder: `AGENTS.*`, `CLAUDE.*`, `AGENT.md`, `GEMINI.md`,
  `SKILL.md`;
- build and tool settings in any folder: `pyproject.toml`, `setup.py`, `setup.cfg`,
  `conftest.py`, `sitecustomize.py`, `usercustomize.py`, `*.pth`, `mypy.ini`,
  `pytest.ini`, `pytest.toml`, `tox.ini`, `ruff.toml`, `uv.toml`, `Dockerfile`,
  `Dockerfile.*` and `CODEOWNERS`;
- at the root: `uv.lock`, `mkdocs.yml` and `docker-compose.yml`.

`gate.control` adds more, such as `scripts/**` or a folder of decision records. It cannot
remove a built-in one. The built-in settings are those of a Python project; list the
settings of other tools in `gate.control`.

A pull request that changes a control file is strict. If its author is not trusted, it
never passes: its own checks may have run with its own copy of the rules. A maintainer
opens the change again from a branch of the repository.

## Who is trusted

- The owner, members and collaborators, as GitHub reports the author's association.
- A bot account listed in `gate.trusted_bots` (Dependabot by default), when the pull
  request comes from a branch of the repository itself.
- The workflow account `github-actions[bot]`, when the pull request comes from a branch
  of the repository itself.

Trust changes two things. A change to a control file can pass only for a trusted author.
A check that ended as `skipped` or `neutral` passes for a trusted author. For anyone
else the gate keeps waiting for a run that ends as `success`.

## The verdict

| Case | The gate says |
|---|---|
| It could not check | `failure`, and the job fails. Run it again. When even the head commit cannot be found there is no status, only the failed job. |
| A control file changes and the author is not trusted | `failure` |
| Light | `success`, unless a `gate.light_blocks` status failed (`Review labels` by default): a finding a reviewer did post still needs its label. |
| Strict, a status or check failed | `failure`, naming each one |
| Strict, a status or check is missing or not finished | `pending`, naming each one |
| Strict, all passed | `success` |

A missing status or check is "not finished". It is never a pass. When the names do not
fit in the status line, the line gives their number and the run's log names each one.

The gate posts nothing for a closed pull request, for one into another branch than the
default one, or for a fork's pull request whose head commit is also the head of a branch
in the repository ([a status belongs to a commit](security.md#a-status-belongs-to-a-commit)).
Only a run that breaks posts a `failure` there.

One limit is known. The guard covers a head that a branch in the repository has. It does
not cover two pull requests from forks. A member opens a pull request from a fork. An
outside author opens another pull request on the same head commit. The outside pull
request's verdict lands on that commit and can turn its status to `failure`. It can never
turn it green. If that happens, run the gate again for the member's pull request, or push
a commit to it.

## What it trusts

- A status counts only when the workflow account `github-actions[bot]` posted it. The
  newest one counts.
- A check counts only under its name and from the app whose id `gate.checks` gives. A
  check of the same name from another app does not count.
- The changed files are read by the two commits (base and head), not by the pull
  request's number. The list must cover as many commits and files as the pull request
  reports. A list that cannot be read whole counts as a control change: strict, never light.
- `devloop` and `devloop.toml` come from the default branch, so a pull request cannot
  change the rules it is judged by. A change to `[gate]` takes effect once it is merged.

Any workflow on a branch of the repository can post a status as the workflow account
([statuses any workflow can post](security.md#statuses-any-workflow-can-post)). Protect
`.github/workflows/` with a code-owner review, as for every required status.

## When it runs

Call `gate.yml` as the last job of each workflow that posts something the gate reads:

```yaml
gate:
  needs: [labels]
  if: always()
  uses: Jon-275/dev-loop/.github/workflows/gate.yml@<tag>
  permissions: { checks: read, contents: read, pull-requests: read, statuses: write }
  with:
    devloop: "git+https://github.com/Jon-275/dev-loop@<tag>"
    pr: ${{ inputs.pr }}   # only in a caller with a manual trigger
```

That is the workflow of the model reviews (a push, an open or reopen, a label) and the
workflow that runs `labels.yml` on reviews and replies. If the gate reads an app's check,
also start it when that check finishes (`check_run`, `completed`). Add a manual trigger
(`workflow_dispatch` with a `pr` input) to one caller, to run the gate by hand.

Only one run judges a pull request at a time, so an older answer is not posted over a
newer one.

Two events do not start the gate for a pull request from a fork:

- A review or a reply. That event's token is read-only for a fork, so it cannot write a
  status. Skip the gate job there:
  `github.event.pull_request.head.repo.full_name == github.repository`.
- A check run finishing. That event does not name a fork's pull request.

The next push runs the gate, and so does a manual run.

## Rolling it out

1. **Report it.** Add the gate job to the callers and merge. The status appears on every
   pull request. Nothing requires it yet.
2. **Compare it.** On real pull requests, check the gate against the statuses it will
   replace: a strict pull request before and after its reviews pass, a pull request of
   documents only, one from a fork, and one whose branch was updated from the base
   ("Update branch"), because the completeness check compares the number of commits.
   The gate must be `success` only when each of
   those statuses would let the pull request merge.
3. **Require it.** In branch protection, require `Merge gate` and remove the reviewers'
   statuses and `Review labels` from the required list. They still decide every strict
   pull request, through the gate. Keep the code checks required.

Require the status only on the default branch. The gate gives no verdict for a pull
request into another branch. Its head commit shows a gate status only when that commit
also heads a pull request into the default branch, and then it shows that one's status.

If you set `gate.context`, require that name instead. One limit: when the run
breaks before devloop has read its settings (devloop could not be installed, `devloop.toml`
is not valid, or there is no token), the `failure` is reported as `Merge gate`. A status
under the new name is left as it was.

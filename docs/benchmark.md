# Conformance benchmark

The benchmark shows whether a coding agent follows devloop's rules in a real project: it
runs the agent headless on a few tasks and judges each result with devloop's own gates.
It finds an agent whose instruction loading, skill support or behaviour silently
changed.

It runs only on a maintainer's machine, with their own agent login
([decision 0006](decisions/0006-local-benchmark.md)): this repository is public, and an
agent run's transcript can hold anything the agent saw.

## Run it

Install the agent's CLI first (Claude Code: `claude` on the `PATH`, signed in), then:

```bash
devloop bench                       # every task, with Claude Code
devloop bench --task fix-bug        # one task (repeat --task for more)
devloop bench --command /path/to/claude --timeout 600
```

Each task runs in a fresh copy of the fixture project under
`.devloop/bench/<time>/<task>/work`, next to the agent's log (`agent.log`). The
scorecard is printed and saved as `scorecard.md` and `scorecard.json`. Exit 0 means
every check passed, 1 a check failed, 2 the benchmark could not run (for example, the
agent CLI is missing). The logs stay on your machine; do not paste them anywhere public.

The agent runs in print mode (`claude -p`) with file edits accepted and pre-approved to
run only `git`, Python and `devloop`; any other command is denied, since print mode
cannot ask. It is limited to 50 turns and saves no session. It uses your own Claude Code
login, so your own settings, `CLAUDE.md` and plugins load as well: a result that differs
from another maintainer's can come from that.

## The fixture

A tiny ledger project with a seeded bug (refunds are dropped from the total), records in
two older formats, and two review findings, one right and one wrong. It is set up with
devloop's own layout for the agent under test: `devloop.toml`, `AGENTS.md` and the
skills, committed on `main`.

## Tasks and checks

| Task | Asks the agent to |
|---|---|
| `fix-bug` | fix the refunds bug |
| `guarded-field` | add an optional `currency`, with older records still loading |
| `review-round` | answer both findings: fix the right one, reject the wrong one |

Every task is judged by these checks; `guarded-field` adds "old records load", and
`review-round` adds "replies labelled" and "findings judged right".

- **tests pass:** the fixture's tests pass.
- **test fails without the fix:** a test file was added or changed, and the tests fail
  once the source change is reverted (a timeout does not count).
- **changelog fragment:** a new, valid fragment in `changes/`.
- **committed on a branch:** everything committed, on a branch other than `main`.
- **within the size limit:** the committed branch is within `pr.max_lines`.
- **no type: ignore:** no added line silences the type checker.

The checks are tested in CI with scripted solutions, one conforming and one per broken
rule, so they need no agent to stay correct.

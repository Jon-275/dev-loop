# AGENTS.md

The rules every coding agent working in this repository reads first. Blocks between
`devloop:begin` and `devloop:end` markers are managed by devloop: `devloop sync` rewrites
them from `devloop.toml`, so change them there, not here. Everything else is this
project's own text, which devloop never touches.

<!-- devloop:begin rules -->
## Working rules

- **Enforced in CI, not by the agent.** Git hooks and CI are the gate for every rule that
  must hold; instructions here explain them. A failing gate is fixed, never bypassed.
- **Keep it simple.** Write clear, concise code, docs and messages in plain language.
  Prefer the simplest change that works.
- **Tests first.** Every fix comes with a test that fails when the fix is reverted.
- **Small PRs.** One concern per PR, about 800 changed lines or fewer (generated and
  lock files excluded). Split larger work into a stack of PRs that each merge on their own;
  `large-pr-approved` is for a change that cannot be split, and the PR says why.
- **Types on every function, docstrings on every source function**; never suppress a
  type error, fix its cause.
- **Never hide a finding or a figure.** Suppressed or excluded items are counted and named.
- **Stateful changes start with their invariants**: write the states, identities and
  invariants first, then an end-state test, then the code.
<!-- devloop:end rules -->

<!-- devloop:begin review -->
## Review

- Review locally before opening a PR, and fix what the review finds.
- Wait for every reviewer to finish on the latest commit, answer every thread, then push
  once: one push per review round.
- Start each thread reply with one of `accepted:` or `rejected:`, then say what changed or why not.
<!-- devloop:end review -->

<!-- devloop:begin coordination -->
## Working alongside other agents

- One worktree per task, and one writer per set of files.
- Plan stacked PRs up front. When merging one:
  - merge with a merge commit, not a squash, so the next PR needs no rebase;
  - first move the next PR's base to `main` (`gh pr edit <next> --base main`), and only
    then delete the merged branch: deleting a PR's base branch closes that PR;
  - rebase a PR or merge `main` into it only when GitHub reports a conflict or `main`
    changed code it depends on, since every push re-runs CI and every reviewer.
- Leave a hand-off note per branch in `.devloop/notes/<branch>.md` (never committed).
<!-- devloop:end coordination -->

## About this project

A standard review and delivery loop across coding agents. Review rules come from one
source and are enforced in the CI/CD process.

- `src/devloop/`: the CLI. `config.py` is the `devloop.toml` schema, `tools.py` lists
  where each tool reads instructions and skills, and `sync.py`, `doctor.py` and
  `init.py` are the commands.
- `src/devloop/templates/` and `src/devloop/skills/`: what devloop writes into a project.
  This repository uses them too, so `devloop doctor --check` must pass here.
- `docs/decisions/`: decisions that bind the code. Read the relevant one before changing
  that area.

Never break this: sync never changes a file it did not write, and it writes nothing if
any source is invalid.

## Commands

```bash
uv sync                          # install
.venv/bin/ruff check .           # lint, 0 errors
.venv/bin/ruff format --check .  # format, 0 issues
.venv/bin/mypy                   # strict, source and tests, 0 errors; no type: ignore
.venv/bin/python -m pytest       # tests, including the installed-wheel test
.venv/bin/devloop doctor --check # this repository's own layout has not drifted
```

When you change a tool's locations in `src/devloop/tools.py`, re-check them against that
tool's own docs and update the sources there and in `docs/tool-matrix.md`.

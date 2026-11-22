# AGENTS.md

The rules every coding agent working in this repository reads first. Blocks between
`devloop:begin` and `devloop:end` markers are managed by devloop: `devloop sync` rewrites
them from `devloop.toml`, so change them there, not here. Everything else is this
project's own text, which devloop never touches.

<!-- devloop:begin rules -->
## Working rules

- **Enforced in CI, not by the agent.** Git hooks and CI are the gate for every rule that
  must hold; instructions here explain them. A failing gate is fixed, never bypassed.
- **Tests first.** Every fix comes with a test that fails when the fix is reverted.
- **Small PRs.** One concern per PR, about $max_lines changed lines or fewer (generated and
  lock files excluded). Split larger work into a stack of PRs that each merge on their own;
  `$large_label` is for a change that cannot be split, and the PR says why.
- **Types and docstrings** on every function; never suppress a type error, fix its cause.
- **Never hide a finding or a figure.** Suppressed or excluded items are counted and named.
- **Stateful changes start with their invariants**: write the states, identities and
  invariants first, then an end-state test, then the code.
<!-- devloop:end rules -->

<!-- devloop:begin review -->
## Review

- Review locally before opening a PR, and fix what the review finds.
- Wait for every reviewer to finish on the latest commit, answer every thread, then push
  once: one push per review round.
- Start each thread reply with one of $labels, then say what changed or why not.
<!-- devloop:end review -->

<!-- devloop:begin coordination -->
## Working alongside other agents

- One worktree per task, and one writer per set of files.
- Plan stacked PRs up front.
- Leave a hand-off note per branch in `.devloop/notes/<branch>.md` (never committed).
<!-- devloop:end coordination -->

## About this project

What the project is, who uses it, and what must never break.

## Commands

How to install, test, lint and type-check. Every agent runs these before declaring done.

# Changelog

## [Unreleased]

### Breaking changes

### Added

### Changed

### Fixed

### Security

## 0.2.0 - 2026-11-22

### Added

- **`devloop changelog`.** Checks changelog fragments in `changes/` and builds them
  into `CHANGELOG.md`, with sections and the paths that need an entry set in
  `[changelog]`.
- **`devloop changes`.** Tells CI whether a pull request changes only documentation
  (`[docs]` in `devloop.toml`), and which tests that read documents must still run.
- **`devloop main-health`.** After a push to main, opens one "main is red" issue per
  failing workflow, comments while it stays red and closes it when the workflow is green.
- **`devloop pr-open`.** Refuses a push to a branch whose pull request already merged or
  closed, so late commits never land on a finished branch. Runs by hand or as git's
  pre-push hook (`--git-pre-push`).
- **`devloop pr-open --install`.** Installs the guard as git's pre-push hook, in
  `core.hooksPath` when set, behind pre-commit's hook when pre-commit owns pre-push, and
  never over another tool's hook.
- **Shared workflows.** `pr-hygiene.yml` runs doctor, size, changelog and changes
  for any project, and `main-health.yml` keeps its "main is red" issue. dev-loop's own
  CI now calls both.
- **`devloop size`.** In CI, counts a pull request's changed lines and fails one
  over `pr.max_lines` unless it has the `pr.large_label` label. Skill copies, `pr.exclude`
  paths and `pr.exclude_blocks` are not counted.

## 0.1.0 - 2026-11-22

### Added

- `devloop init`, `sync` and `doctor`: one `AGENTS.md` and one set of skills,
  rendered for Claude Code, Codex, Gemini CLI, Antigravity CLI, Cursor and Copilot.

# Changelog

## [Unreleased]

### Breaking changes

### Added

### Changed

### Fixed

### Security

## 0.3.0 - 2026-11-22

### Added

- **`devloop review --provider NAME`.** Reviews a pull request with a model and posts
  each blocking finding as an inline thread, ported from gtm_toolkit's review core: the
  same prompt, reply rules, thread markers and status names, so existing threads keep
  counting. Nothing is hidden: the summary names every finding without a thread, every
  concern that is not a finding, and every file that was not reviewed (which fails the
  status). After a completed review only the pull request's own changes are reviewed
  again, and a diff too long for one reply is reviewed in parts.
- **Reviewers.** Gemini, Reflection and any OpenAI-compatible API, with the standard
  library only, and Anthropic (`claude-opus-5-5` by default) through the official SDK as
  the optional extra `dev-loop[anthropic]`. A project sets each reviewer's model,
  endpoint, key variable and dimensions in `[review.reviewers.NAME]`, and its lens in
  `review.lenses_dir`.
- **Routing and blocking.** `[tiers]` and `review.providers` decide which reviewers a pull
  request gets; findings below `review.blocking_severities` (P3 by default) are listed in
  the summary only. `review.files` and `review.high_effort` set what is reviewed and at
  which effort. Each summary reports the tokens the review used.
- **`devloop review --local`.** Reviews the current branch and prints the findings.
- **`review.yml`.** A shared workflow for `pull_request_target` callers: devloop and its
  settings come from the base branch's checkout, and the pull request is only read through
  Git. dev-loop reviews its own pull requests with it.

### Security

- **A pull request cannot hide its code from a model reviewer.** Review diffs ignore the
  pull request's `.gitattributes` (`-diff` turned code into "Binary files differ"), and a
  changed file whose diff and contents both missed the prompt is listed as unreviewed.

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

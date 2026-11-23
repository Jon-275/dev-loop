# Changelog

## [Unreleased]

### Breaking changes

### Added

### Changed

### Fixed

### Security

## 0.4.0 - 2026-11-23

### Added

- **Metrics per authoring tool.** `devloop agent --install` adds a `commit-msg` hook that
  appends an `Agent: <tool>/<model>` trailer when `DEVLOOP_AGENT` is set (it never blocks a
  commit), and `devloop metrics` reports each tool's pull requests, time to merge, threads,
  review rounds, rejected share and size. The shared `metrics.yml` runs it weekly; dev-loop
  runs it on itself.
- **`devloop feedback`.** Collects what maintainers did with review findings into the
  `review.feedback` store: a resolved finding thread with a member's labelled reply
  (`labels.prefixes`, such as `accepted:` or `rejected:`) becomes a record, and a finding
  rejected three times is suppressed (never a P1). Nothing is written when any read fails,
  and an unreadable store is never overwritten. Ported from gtm_toolkit.
- **`devloop labels` and `labels.yml`.** The "Review labels" status: every review finding
  on a pull request needs a member's labelled reply, resolved or not, and the status stays
  pending until every review in `labels.wait_for` and `labels.wait_for_checks` has
  finished. A failed read posts a failure, never a pass. dev-loop checks its own pull
  requests with it. Ported from gtm_toolkit.
- **`devloop lessons`.** Ranks the bug classes review found (seeded in `lessons.seed`,
  counted from accepted findings in `review.feedback` and council notes) and writes the
  lessons page (`lessons.doc`), the top 10 rules into `lessons.agents` between the
  `lessons:start` and `lessons:end` markers, and the patterns every reviewer checks first
  (`review.lessons`). An input that exists but cannot be read writes nothing. Ported from
  gtm_toolkit.
- **`devloop metrics`.** Measures the review loop over the pull requests merged in a window:
  time to merge, size against `pr.max_lines`, finding threads by reviewer and severity,
  labels, precision, unique accepted findings, review app reviews and model tokens. Nothing
  is dropped silently: truncated pull requests and unmeasured tokens are named. Ported
  from gtm_toolkit.

### Fixed

- **A concern after a closed hole is still listed.** "This closes the gap where tokens
  could leak despite tests failing" was read as a description; only the hole up to its
  damage verb is masked now, so a concern word after it still lists the sentence
  (Greptile on gtm_toolkit #272).

## 0.3.1 - 2026-11-23

### Added

- **Replay a gtm_toolkit pull request.** `scripts/replay.py` and the hand-run
  `replay.yml` run one pull request through gtm_toolkit's reviewers and devloop's and
  compare them: prompts must be identical, findings are matched by severity, file and
  nearby line. `[review.reviewers.NAME] summary` sets a reviewer's summary instruction,
  and three or more dimension tags read as a range ("D1-D9"), so Reflection's prompt is
  gtm_toolkit's byte for byte.

### Fixed

- **A summary sentence that only describes the change is not listed as a concern.**
  "This closes the paths where a pull request's `.gitattributes` could hide changed
  content" and "The change extends loop metrics to capture failed model attempts" no
  longer appear under "Not posted as findings". Only whole sentences of the live shapes,
  with closed word lists, are read as descriptions; anything else that names a concern is
  still listed.

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

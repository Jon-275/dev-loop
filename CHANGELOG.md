# Changelog

## [Unreleased]

### Breaking changes

### Added

### Changed

### Fixed

### Security

## 0.8.0 - 2026-11-24

### Added

- **A lesson can name the check that enforces it.** A seed class with `enforced_by` is left out
  of the prose rules in `AGENTS.md` and named there in one line with its check, so the block
  shrinks as lessons become lint rules or tests. The lessons page and the reviewers keep the rule.

## 0.7.1 - 2026-11-24

### Fixed

- **The benchmark judges the checkout the agent worked in.** An agent that followed
  AGENTS.md and worked in a worktree under `.worktrees/` failed three checks, because only
  the main checkout was read; the one worktree with new commits on a branch is now judged.
  The fixture ignores `.worktrees/` and `review/replies.json` (the replies stand for
  comments on the pull request), and the replies are read from either checkout.

## 0.7.0 - 2026-11-24

### Added

- **Council drift warnings.** `devloop doctor` and `devloop council check` warn, without
  failing, when a dependency or file calls for a library pack no role card covers (for
  example `stripe` for payments), or a decision in `docs/decisions/` is enforced by no
  role. `council.declined` lists the packs the owner chose not to add.
- **`devloop council report` judges a council report.** The `council-review` skill's
  report now starts with frontmatter (pull request, head commit, tier, reviewers, verdict,
  findings per severity). The command judges it for the head: success only for a safe
  report on that exact commit, at the change's tier or riskier, from every summoned
  reviewer, with no P0 or P1; anything not judged is pending, never a pass.
- **The `Council review` status.** `devloop council report --post --pr <N>` posts a
  report's verdict on the pull request's head, read from GitHub. The shared `council.yml`
  workflow keeps it current on every push: pending while the head's tier needs a council,
  success where none is required, never overwriting a verdict already on that head. Tier 1
  can now require a council review whichever tool ran it.

### Fixed

- **Hand-off notes stay out of git, and the council's record has one name.** `devloop sync`
  writes `.devloop/.gitignore` (`notes/`), so the notes `AGENTS.md` says are never
  committed are ignored, without editing the project's own `.gitignore`. The
  `council-design` skill and the council docs name the council's decision record the way
  `decision-record` names every record (`docs/decisions/NNNN-council.md`). Both found
  adopting devloop in a second repository.

## 0.6.0 - 2026-11-24

### Added

- **The conformance benchmark's tasks and checks.** Three tasks (fix a bug, add a guarded
  field, answer a review round) and the checks that judge an agent's result: tests that
  fail without the fix, a changelog fragment, a feature branch, the size limit, no
  `type: ignore`, labelled and correct review replies.
- **The conformance benchmark's fixture project.** `devloop.bench.fixture.materialize`
  writes a small ledger project with a seeded bug into a scratch directory, with devloop's
  layout for one tool, committed on `main`. The benchmark runs locally only (decision
  0006).
- **`devloop bench` runs the conformance benchmark.** It runs Claude Code headless on each
  task in a fresh copy of the fixture, under a time limit, and prints a scorecard that
  counts and names every failed check; the agent's transcript stays in a local log. Local
  only (decision 0006); the `release` skill runs it for devloop's own releases.

## 0.5.2 - 2026-11-23

### Fixed

- **The weekly metrics can read commit statuses.** `metrics.yml` asks for
  `statuses: read`: `devloop metrics` reads each commit's review statuses, and without that
  permission GitHub refused the whole query (`FORBIDDEN`), so the job failed on its first
  scheduled run. Callers grant `statuses: read` too.

## 0.5.1 - 2026-11-23

### Added

- **`devloop size --base <ref>` measures a branch locally.** It counts the committed
  branch from where it left `<ref>`, with the same exclusions as the CI check, and exits 1
  over `pr.max_lines`. The `pr-workflow` and `split-stack` skills use it instead of
  `git diff --shortstat`, which counted lock and generated files; `devloop-setup` now says
  how to install devloop (Greptile on gtm_toolkit #274).

## 0.5.0 - 2026-11-23

### Added

- **The `testing-and-ci` and `python-package` skills.** Generalised from gtm_toolkit's
  testing and packaging skills: tests that fail when their fix is reverted, the installed
  wheel checked outside the checkout, CI lanes that fail closed and never run pull request
  code with secrets, and a typed package proven from its wheel.
- **Council role cards.** One Markdown file per role in `council.roles_dir` (default
  `devloop/council/`) says when the role is summoned (`summoned_by` globs and `tiers`),
  which reviewer runs it, and its mandate, checks, severity anchors and proof methods.
  `devloop council plan` prints the roles and reviewers a change summons; `devloop council
  check` and `devloop doctor` report a bad card, a glob that matches no file, a decision
  file that does not exist, and a council with more reviewers for a tier than
  `council.max_reviewers` allows.
- **A role library and the council skills.** devloop ships 17 role cards: six every
  council starts with (architect, test and quality, security, operability, claims and
  docs, editor) and eleven domain packs (ML and statistics, data engineering, CRM and
  RevOps, GTM strategy, payments, regulated data, frontend UX, mobile, infrastructure, LLM
  applications, public APIs), which fit the default reviewer budget together.
  `devloop council library` lists them and `devloop council add <role>` copies one into
  the project to specialise. The `council-review` skill reviews a change with the roles
  its paths summon, every P0 and P1 proven by a command; `council-design` drafts a
  project's council from its signals and the owner's answers and proves it on known
  defects.
- **The security model, council guide, cost model and upgrade guide.** The docs site now
  says what a trusted review may and may not run, what each model vendor receives (devloop
  does not redact secrets), why `.github/workflows/` needs code owners, how to write and
  prove a council, what each push costs and which settings lower it, and how to upgrade.
  Three framework decisions are recorded: every check fails closed, every finding gets a
  labelled reply, and no finding or figure is hidden.
- **A documentation site.** `mkdocs build --strict` builds `docs/` with no warnings, and
  CI runs it on every pull request. New pages: concepts (the loop enforced in CI, risk
  tiers, labels and lessons, metrics and what "aligned" means), adoption (install, shared
  workflows, secrets, branch protection, agents' identity, the council), a configuration
  reference for every `devloop.toml` key, and the skills devloop ships. Tests keep the
  configuration page and the skills page exactly in line with the code.
- **Eight workflow skills.** `pr-workflow`, `review-locally`, `address-review`,
  `split-stack`, `stateful-change`, `decision-record`, `lessons-intake` and `release` ship
  with devloop and render for every tool. A new project includes every shipped skill
  (`skills.include` defaults to all of them). Each skill names no agent-specific tool,
  every `devloop` command and flag it names exists, and each says when not to use it;
  tests enforce all three.

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

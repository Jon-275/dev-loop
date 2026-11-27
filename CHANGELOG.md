# Changelog

## [Unreleased]

### Breaking changes

### Added

### Changed

### Fixed

### Security

## 0.13.0 - 2026-11-27

### Breaking changes

### Added

- **`enforce_admins` is judged per source.** `devloop doctor --repo` says administrators are held to the rules only when classic protection and every ruleset that supplies an expected rule hold them. A ruleset that requires the checks but lets administrators bypass it now differs, and the line names it.
- **`devloop doctor --repo OWNER/NAME` checks the repository's GitHub settings.** It reads the live settings (branch protection and rulesets, the Actions permissions, secret scanning, merge methods and more) and compares them with `[github]` in `devloop.toml`. Each setting is ok, differs (expected and found) or not judged (with the reason, such as a token without admin rights). A branch rule counts when classic protection or a ruleset makes it, for administrators too. It only reads. The exit code is 1 when something differs and 3 when something could not be judged; `--json` prints records. See docs/github-settings.md.
- **`gate.yml` takes the status's name as a `context` input.** It defaults to `Merge gate` and must be the same as `gate.context`; `devloop gate --context` refuses to start when they differ. A run that breaks at any point reports under that name, where before a run that broke before devloop read its settings reported as `Merge gate`. Runs queue per name, so a second gate can be reported beside another status of the default name without sharing its queue or its failure reports.
- **A `[github]` table in `devloop.toml`.** It holds the repository settings a project expects on GitHub (required checks, a pull request before merging, administrators, force pushes, the workflow token, secret scanning, merge methods and more). The defaults are the baseline of `docs/github-settings.md`, and any key can be `"skip"`. `devloop doctor --repo` will read it.
- **A GitHub settings baseline.** `docs/github-settings.md` lists the repository settings devloop's checks rely on in one table: the value, the reason, where it is set and the API field that shows it. It also says what it leaves out for a one-maintainer project, and why.
- Add opt-in shared validation and size workflows so label changes remeasure size without replacing required code and validation results.
- **dev-loop's releases are tagged and published by a workflow.** When a push to main changes the version in `pyproject.toml` to one that has no tag, and `CHANGELOG.md` has that version's `## <version> - <date>` section, `.github/workflows/release.yml` creates the tag `v<version>` on that commit and publishes a release with the section as its notes. It does nothing on any other push, never moves a tag, and fails without creating anything when the section is missing or the version is not `X.Y.Z`. Two commands hold the decision: `devloop release-notes [--version X.Y.Z]` prints a version's changelog section, and `devloop release-due` answers for the current commit. The `release` skill now says to let the workflow tag, and how to publish by hand when it fails.
- **`review.max_rounds`: a cap on reviews per pull request.** Each push re-runs every
  reviewer, and nothing limited how often. With `max_rounds` set, once a reviewer has
  posted that many reviews on a pull request the next run sends nothing to the model and
  its status fails with "Not reviewed: cap of N reviews reached": never a pass, never a
  silent skip. A maintainer adds the `review-again` label (`review.yml`'s new
  `again_label` input) for one more review; on an outside pull request the approval
  label is still needed first. A round is a review the workflow account posted, so an
  author cannot forge or reset the count. The default, `0`, is no cap and changes
  nothing. `docs/cost.md` now says plainly that devloop has no spend budget.
- **`settings-check.yml`, a shared workflow that checks the repository's settings on a schedule.** It runs `devloop doctor --repo` and keeps one issue, "Repository settings differ from devloop.toml": opened or updated with the report when a setting differs or was not judged, closed when a later run is clean. It may read the repository and write issues, nothing else. An optional `token` secret lets it read admin settings; without it those are reported as not judged. dev-loop calls it weekly. One run per repository works on the issue at a time, and its calls to GitHub retry a transient failure as the other shared workflows do. Creating the issue is never sent again without first looking whether it is there, so a lost reply cannot open a second one.
- **A read-only reader of a repository's GitHub settings.** It reads the repository, the default branch's protection, the rulesets that cover it, the Actions permissions and private vulnerability reporting, with GET calls only and the retries every devloop call has. An answer GitHub refuses keeps its reason, so `devloop doctor --repo` can report that setting as not judged. A ruleset's branch patterns are matched as GitHub matches them (`*` stays inside one part of the name, `**` crosses `/`), and a ruleset that cannot be placed is a reason, not "does not apply".
- **A page on writing a status check.** `docs/status-checks.md` lists the twelve rules a workflow that posts a required commit status must follow, each with the failure it prevents and a link to where this repository does it: guard before any write, one writer of pending, "could not check" is a failure, one run at a time, judge the current head, trusted code only, decide from the changed files, count only the expected account's signals, pin the whole job in a test, roll out in three steps, two facts about events, and check exit codes.

### Changed

- **The GitHub API requests of `devloop labels` and `devloop gate` are counted and pinned.**
  Each reply on a review thread starts both, and the workflow token may make 1,000 REST
  requests per repository per hour. Tests now pin the requests one evaluation makes (7
  for the labels check; 12 for the gate with two statuses and one check), so a change
  that adds a read fails a test. `docs/cost.md` lists the counts under "GitHub API reads
  per run", with the arithmetic for a busy pull request. No read was removed: each one
  is a guard or a second read that closes a race.
- **`review-locally` and `pr-workflow` say how to check your own work.** `review-locally`: the reviewer must not have seen the author's reasoning (a fresh agent or session given the diff and the rules, not the conclusion), and the second pass covers only the commits since the first and re-runs the earlier probes. `pr-workflow`: before opening, revert the fix in a scratch copy and watch the new test fail, and look at what a user would see (the page, the command, the report); run each gate so its own exit code is seen, never through a filter. Run `devloop sync` after upgrading to get the new text.

### Fixed

- **The docs match the code again, and a test keeps two lists true.** README and the adoption page now list every shared workflow (`unapprove.yml`, `labels.yml`, `gate.yml`, `council.yml` and `metrics.yml` were missing from one or both) and README lists every command (`devloop bench` and `devloop council report` were missing); `tests/test_docs_lists.py` fails when either list differs from the workflow files or the parser. The security page no longer says secrets are sent unchecked: the review refuses to send text that holds a likely secret, though it does not redact, and outside pull requests wait for approval. The upgrade page's `framework.version` example no longer names an old version.
- **The shared workflows' own GitHub calls retry a transient failure.** One HTTP 502 from
  GitHub turned a job red, and in a step that writes a status it could leave a wrong
  status standing. Every `gh api` call in `review.yml`, `gate.yml`, `labels.yml` and
  `unapprove.yml` now goes through one small shell function, repeated in each step: up to
  three tries, waiting 2 then 4 seconds, on HTTP 429, a 5xx or a lost connection. A 403,
  404 or 422 is never retried. The status guard still fails closed when its read fails
  after the tries, and `unapprove.yml` still treats a 404 as success.

### Security

## 0.12.0 - 2026-11-27

### Breaking changes

- **The model review waits for a maintainer on an outside pull request unless you say otherwise.** `review.yml`'s `gate` input now defaults to `members`: the owner, members, collaborators and `trusted_bots` are reviewed on every push, and anyone else waits for the `ai-review-approved` label. Before, the default reviewed every pull request, so a public project that set nothing let any account spend its key. To keep the old behaviour pass `gate: everyone`; an empty `gate` is now an error. If outside pull requests should be reviewable, trigger on `labeled` and call `unapprove.yml`.

### Added

- **The repository has the files it tells adopters to have.** `SECURITY.md` (private reporting), `CONTRIBUTING.md` (set-up, what a change needs, and when a pull request is sent to the model reviewers), a `CODEOWNERS` file for the workflows and review code, a pull request template and two issue templates.
- **Financial modeling council pack and PR guidance.** Added the `financial-modeling` council library role and workbook dependency/file signals (`openpyxl`, `xlsxwriter`, `xlwings`, `*.xlsx`, `*.xlsm`), documented `exclude_blocks` markers for synced reference tables, and updated `pr-workflow` to pass multi-line PR bodies via `--body-file`.
- **`devloop gate --pr N --repo OWNER/NAME [--run-url URL]`.** It reads `[gate]` from the `devloop.toml` in the folder it runs in, judges the pull request and posts the gate's commit status with `GITHUB_TOKEN`. Exit code 0 means a verdict was posted or none is due, 1 that it could not check, and 2 that it could not start. It writes the status's name to `$GITHUB_OUTPUT` as `context`. `gate.context` must be one line of at most 100 characters.
- **A `[gate]` table in `devloop.toml`, for the merge gate.** It names the gate's commit status, the documents of the light lane, extra control files, the statuses and checks the strict lane needs, the statuses that block the light lane, the trusted bots and the size of a light change. `statuses` defaults to `labels.wait_for` plus `Review labels`.
- **The merge gate reads a pull request and posts its status (`devloop.gate.reader`).** It posts nothing for a pull request that is closed, goes into another branch than the default one, or comes from a fork with a head a branch of the repository also heads (the rule `devloop labels` uses, now shared as `status_guard.head_of`). After those guards it marks the head pending, reads the changed files by the two commits, checks the list is complete, and posts the verdict. A read that fails ends as a `failure`, never a pass.
- **The merge gate's lane rules (`devloop.gate.rules`).** A pull request is in the light lane only when every changed file is a regular document that `gate.light` names, with a plain path, and the list of files is complete and no longer than `gate.max_light_files`. Anything else is strict. Hidden files and folders, `devloop.toml`, `devloop/`, agent instructions, build and tool settings, the folders and files `devloop.toml` names and `gate.control` are control files: they are never documents, and a change to one is flagged.
- **The merge gate's verdict rules.** A strict pull request passes only when every status in `gate.statuses` and every check in `gate.checks` passed; a failed one is a failure that names it, and one that is missing or not finished is pending. A light pull request passes unless a `gate.light_blocks` status failed. A change to a control file by an author who is not trusted never passes. A status counts only from the workflow account, and a check only from its configured app.
- **`gate.yml`, a shared workflow for the `Merge gate` status.** Call it as the last job of the workflows that post reviews and labels, and require its one status in branch protection in place of one per reviewer: a pull request of documents only passes without waiting for a reviewer, and any other needs every status and check of `[gate]` on its head. It installs devloop and reads `devloop.toml` from the default branch, holds no secret, runs one evaluation of a pull request at a time, and never posts pending or success from the shell. dev-loop's own review workflows now call it. See docs/gate.md for the lanes, the control files and how to roll it out.
- Add a standalone reusable drift workflow for branch protection that preserves its verdict across pull request label events.
- **`unapprove.yml`, a shared workflow that removes the approval label on every push.** Approval covers one head, and each project had to write this job itself. It holds no key and checks out nothing, and it succeeds when another push has already removed the label.

### Changed

- Add a model-validation skill and document how to verify and publish matching model artifacts and test persisted results for stale inputs and interrupted runs.
- **Commit attribution requires opt-in.** The installed hook adds trailers only when the repository sets `agents.commit_attribution = true` and supplies a valid attribution value. Set this option when upgrading a project that wants to continue using trailers.

### Fixed

- Pass the reusable approval-withdrawal workflow its declared read permission so GitHub can start the required reviewer jobs.

### Security

- **dev-loop's own model reviews wait for a maintainer on an outside pull request.** `review-trusted.yml` now calls the shared review with `gate: members`, triggers on `labeled`, and removes the `ai-review-approved` label on every push. Before, a pull request from any account was sent to both reviewers on the repository's keys.
- **A pull request does not choose its own rules.** In `pr-hygiene.yml`, `devloop changes`, `devloop size` and `devloop changelog check` now read `devloop.toml` from the base commit (`--config-from HEAD^1`). A pull request can no longer widen `docs.files`, raise `pr.max_lines`, add to `pr.exclude` or move `changelog.dir` for its own run; a change to these settings takes effect from the next pull request. A base file that cannot be read fails the check. Only a base with no `devloop.toml` falls back to the pull request's file. A change to `devloop.toml` always counts as code. `pr-hygiene.yml` and `main-health.yml` pin the same uv and Python versions as the other shared workflows.
- **A fork's pull request can no longer replace a status on a branch's head commit.** A
  commit status belongs to a commit. A pull request from a fork whose head is also the
  head of a branch in the base repository could overwrite `Review labels`, a
  `<Provider> review` or `Council review` there, and turn a failure green. The shared
  workflows and `devloop council report --post` now write no status for such a pull
  request, say why in a notice, and do not run its review. `labels.yml` no longer posts
  pending from the shell; `devloop labels` posts it after the check. The gate job of
  `review.yml` now needs `contents: read`, for one read of the branches. See "A status
  belongs to a commit" in docs/security.md.

## 0.11.0 - 2026-11-25

### Added

- **Repository control over commit attribution.** Set `agents.commit_attribution` to `false` to turn off the commit trailer in the installed hook and the generated guidance.
- **The council designs a risky change before it is written.** The new `council-build`
  skill puts the council's roles on a tier-1 or tier-2 change first: a short brief,
  `devloop council plan --paths` for the paths the change will touch, then each summoned
  reviewer gives a design for its own area (what must hold, what the reader sees, the
  decisions that bind it, the tests that prove it). The author folds them into one design
  and takes any choice that is the owner's to the owner before building. `AGENTS.md` gains
  the working rule, and `pr-workflow` the step. `council-review` still judges the result.

## 0.10.1 - 2026-11-25

### Changed

- **A red `Review labels run` job now always means the check could not run.** The verdict
  is the `Review labels` commit status. The job used to fail as well whenever a thread had
  no labelled reply, and each new reply cancelled the run before it, so a commit collected
  failed and cancelled jobs even after its status turned green. Now `devloop labels` exits
  0 whenever it posted a verdict, runs for one pull request overlap instead of cancelling
  each other, and each run reads the threads again after posting, so the last one to
  finish leaves the status that matches them. A bot's comment events no longer start a run
  each: its review event starts one.

## 0.10.0 - 2026-11-24

### Security

- **A review refuses to send a likely secret.** Before the first request, the diff and
  the file contents a review would send are checked for provider key formats and
  private-key headers. On a match nothing is sent, the check fails, and the run names
  the file, the line and the kind of key, never the text. On by default; exempt test
  files with `review.secret_allow`, or set `secret_scan = false`.
- **An approved outside pull request has a size limit.** With `gate: members`, a pull
  request reviewed because a maintainer approved it may send at most
  `review.outside_max_chars` characters (150,000 by default); over it, nothing is sent.

## 0.9.0 - 2026-11-24

### Security

- **`review.yml` can hold a model review until a maintainer approves it.** With
  `gate: members` a pull request from the owner, a member, a collaborator or a trusted
  bot is reviewed as before; anyone else's waits, its status pending, until a maintainer
  adds the `ai-review-approved` label, which reviews that head once. A stranger can no
  longer spend the project's key or have their text sent to a reviewer by opening a pull
  request. The default (no gate) is unchanged.

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

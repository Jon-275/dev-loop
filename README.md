# dev-loop

A standard review and delivery loop across coding agents. Review rules come from one
source and are enforced in the CI/CD process.

It works with Claude Code, Codex, Gemini CLI, Antigravity CLI, Cursor and Copilot.

## How it works

- **One source.** Rules live in `AGENTS.md` and skills in one folder. devloop copies
  them to where each tool looks.
- **Enforced in CI.** Git hooks and CI check the rules, so no tool can skip them
  ([decision 0001](docs/decisions/0001-enforce-in-git-and-ci.md)).
- **No drift.** `devloop doctor --check` fails when a copy differs from its source.

## Get started

```bash
uv tool install git+https://github.com/Jon-275/dev-loop@v0.4.0
cd your-project
devloop init              # set up the project
devloop doctor --check    # check nothing has drifted; run this in CI
```

## Commands

- `devloop init`: create `devloop.toml` and set up the project.
- `devloop sync`: update every copy from its source. Edit the source, never a copy.
- `devloop doctor [--check]`: list any problems. `--check` exits 1 if there are any.
- `devloop size`: in CI, count a pull request's changed lines and fail one over
  `pr.max_lines`, unless it has the `pr.large_label` label. Skill copies and the paths
  in `pr.exclude` are not counted.
- `devloop changelog check|build`: check the changelog fragments in `changes/`, or add
  them to `CHANGELOG.md` (see [changes/README.md](changes/README.md)).
- `devloop changes`: in CI, say whether a pull request changes only documentation, so
  the code checks can be skipped.
- `devloop pr-open [branch ...]`: refuse to push to a branch whose pull request has
  already merged or closed. Set `ALLOW_PUSH_TO_FINISHED_PR=1` to push anyway.
  `devloop pr-open --install` makes it git's pre-push hook, once per clone.
- `devloop main-health`: after a push to main, open a "main is red" issue for a failing
  workflow and close it when the workflow passes again.
- `devloop review --provider NAME`: review a pull request with a model and post each
  blocking finding as a thread (P3 findings go in the summary only). `--local` reviews
  the current branch and prints the findings. See [docs/review.md](docs/review.md).
- `devloop feedback`, `devloop labels`, `devloop lessons`: collect labelled review
  findings, require a label on every finding thread, and rank the bug classes review
  found into the rules every agent reads ([concepts](docs/concepts.md)).
- `devloop metrics [--days N]`: the review loop's figures for the pull requests merged in
  a window, per authoring tool. `devloop agent --install` adds the `Agent:` trailer.
- `devloop council plan|check|library|add`: the council's role cards
  ([skills](docs/skills.md): `council-design`, `council-review`).

Full documentation: [concepts](docs/concepts.md), [adoption](docs/adoption.md),
[configuration](docs/configuration.md) and [skills](docs/skills.md). Build it with
`mkdocs build --strict`.

## In CI

Three shared workflows run the checks, so every project's CI calls the same code:

- [`pr-hygiene.yml`](.github/workflows/pr-hygiene.yml): `devloop doctor --check`,
  `size`, `changelog check --pr` and `changes`. Its `code` output is `"false"` for a
  docs-only pull request, so the caller can skip its code checks.
- [`main-health.yml`](.github/workflows/main-health.yml): `devloop main-health`, as the
  caller's last job on every push to main.
- [`review.yml`](.github/workflows/review.yml): `devloop review`, one job per reviewer,
  from a caller on `pull_request_target`.

Each file's header shows the job to copy. [dev-loop's own CI](.github/workflows/ci.yml)
calls the first two, and [its reviews](.github/workflows/review-trusted.yml) the third.

## What `init` writes

| File | Purpose |
|---|---|
| `devloop.toml` | Project settings |
| `AGENTS.md` | Shared rules. devloop updates the marked blocks; the rest is yours |
| `CLAUDE.md` | Points Claude Code at `AGENTS.md` |
| `.gemini/settings.json` | Points Gemini CLI at `AGENTS.md` |
| `.claude/skills/`, `.agents/skills/`, `.github/skills/` | Skill copies for each tool |
| `.devloop/manifest.json` | The files devloop wrote, so it never touches yours |

Your own skills go in `devloop/skills/<name>/SKILL.md`. Where each tool looks:
[docs/tool-matrix.md](docs/tool-matrix.md).

## Licence

Apache-2.0. See [LICENSE](LICENSE).

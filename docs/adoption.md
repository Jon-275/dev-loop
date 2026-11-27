# Adoption

## 1. Install and initialise

```bash
uv tool install git+https://github.com/Jon-275/dev-loop@v0.11.0
devloop init
```

`devloop init` writes `devloop.toml` (only when it is absent), the managed blocks of
`AGENTS.md`, every shipped skill for every tool, and the adapters. Edit `devloop.toml`
for anything this project does differently ([configuration](configuration.md)), add your
own skills under `devloop/skills/`, then run `devloop sync` and commit the result.

Once per clone, install the branch hook:

```bash
devloop pr-open --install   # never push to a branch whose PR already merged
```

## 2. Call the shared workflows

Pin each call to a release tag or commit. Each workflow's header shows the job to copy.

| Workflow | Trigger in the caller | What it does |
|---|---|---|
| `pr-hygiene.yml` | `pull_request` | advisory doctor, size, changelog, docs-only detection |
| `drift-check.yml` | separate `pull_request` workflow on code and review lifecycle events | repository drift verdict for branch protection |
| `review.yml` | `pull_request_target` | one job per model reviewer ([model reviews](review.md)) |
| `labels.yml` | after the review jobs, and on review replies | the `Review labels` status |
| `gate.yml` | last job of the review and labels callers | the `Merge gate` status: one required status in place of one per reviewer ([merge gate](gate.md)) |
| `main-health.yml` | `push` to the default branch, last job | opens and closes a "main is red" issue |
| `metrics.yml` | `schedule`, weekly | the weekly report, per authoring tool |

The review caller runs on `pull_request_target`, so it holds secrets: it must never check
out or run the pull request's code. `review.yml` only reads the pull request through git.

## 3. Secrets

| Secret | Used by | Notes |
|---|---|---|
| One API key per model reviewer (for example `GEMINI_API_KEY`) | `review.yml` | passed as `api_key`; only the trusted job sees it |
| `GITHUB_TOKEN` | every workflow | the default token, with the permissions each job's header lists |

No workflow needs a personal access token.

## 4. Branch protection

On the default branch, require a pull request, conversation resolution, and these
statuses:

- the caller's code checks and its dedicated repository drift check;
- `<Name> review` for each model reviewer (for example `Gemini review`);
- `Review labels`.

Or require the one `Merge gate` status in place of the reviewers' statuses and
`Review labels` ([merge gate](gate.md#rolling-it-out)).

A required status is what turns a rule into a gate. The other settings (administrators,
force pushes, the workflow token, secret scanning, the merge method and the rest) are one
table, each with its reason: [GitHub settings](github-settings.md).

Use a separate caller like the [repository drift workflow](https://github.com/Jon-275/dev-loop/blob/main/.github/workflows/repository-drift.yml).
Replace its local workflow call and `devloop: '.'` with the same pinned commit for the
reusable workflow and the installed tool. Use the repository's default branch and keep the `edited` trigger for retargeted PRs. If
using a merge queue, also trigger the caller for `merge_group` with `checks_requested`.

The advisory workflow runs on label events and skips its doctor job on those events.
GitHub can count a skipped job as a pass. Keep label events and conditional skips out of
the dedicated caller. On a disposable PR, prove that an intentional drift failure stays
failed when a label is added and removed, then prove that a repaired revision passes.
Record the check name and source reported by GitHub. Require the verified check before
retiring any previous drift requirement. Audit other required checks for the same skip
behavior. [GitHub's required status checks guide](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks)

## 5. Optional commit attribution

Repositories that want per-tool metrics can set `commit_attribution = true` under
`[agents]` in `devloop.toml`. Run `devloop sync`, install the hook with
`devloop agent --install`, and set `DEVLOOP_AGENT=<tool>/<model>` when committing.
The resulting trailer is visible in the commit message.

## 6. The council

Run the `council-design` skill to draft the project's council from devloop's role library,
and record it as a decision ([the council](council.md)). `devloop doctor` then checks the
cards stay current. Read the [security model](security.md) before adding secrets.

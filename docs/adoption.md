# Adoption

## 1. Install and initialise

```bash
uv tool install git+https://github.com/Jon-275/dev-loop@v0.9.0
devloop init
```

`devloop init` writes `devloop.toml` (only when it is absent), the managed blocks of
`AGENTS.md`, every shipped skill for every tool, and the adapters. Edit `devloop.toml`
for anything this project does differently ([configuration](configuration.md)), add your
own skills under `devloop/skills/`, then run `devloop sync` and commit the result.

Once per clone, install the git hooks:

```bash
devloop pr-open --install   # never push to a branch whose PR already merged
devloop agent --install     # add the Agent: trailer when DEVLOOP_AGENT is set
```

## 2. Call the shared workflows

Pin each call to a release tag or commit. Each workflow's header shows the job to copy.

| Workflow | Trigger in the caller | What it does |
|---|---|---|
| `pr-hygiene.yml` | `pull_request` | doctor, size, changelog, docs-only detection |
| `review.yml` | `pull_request_target` | one job per model reviewer ([model reviews](review.md)) |
| `labels.yml` | after the review jobs, and on review replies | the `Review labels` status |
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

- the caller's code checks and `hygiene / devloop doctor`;
- `<Name> review` for each model reviewer (for example `Gemini review`);
- `Review labels`.

Block force pushes and deletion, and apply the rules to administrators too. A required
status is what turns a rule into a gate.

## 5. Agents' own identity

Agents that open pull requests should do so as a GitHub App rather than as the owner's
account. Then the owner's approval means something, and metrics can tell agent work from
human work. Set `DEVLOOP_AGENT=<tool>/<model>` in each agent's environment so every
commit says which tool wrote it.

## 6. The council

Run the `council-design` skill to draft the project's council from devloop's role library,
and record it as a decision ([the council](council.md)). `devloop doctor` then checks the
cards stay current. Read the [security model](security.md) before adding secrets.

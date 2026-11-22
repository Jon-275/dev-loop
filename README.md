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
uv tool install dev-loop
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

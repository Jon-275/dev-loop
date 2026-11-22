# dev-loop

One review and delivery loop for every coding agent. Claude Code, Codex, Gemini CLI,
Antigravity CLI, Cursor and Copilot all work to the same rules, because those rules come from one source
and are enforced where no tool can skip them.

## The four layers

1. **Enforcement**: git hooks, CI and required statuses. The same for every tool, and the
   only real gate ([decision 0001](docs/decisions/0001-enforce-in-git-and-ci.md)).
2. **Measurement**: loop metrics per authoring tool, to show when one drifts (planned).
3. **Instruction**: one `AGENTS.md` and one set of skills, rendered into every tool's
   location.
4. **Adapters**: thin per-tool files that point at the instruction layer.

## Five-minute adoption

```bash
uv tool install dev-loop     # or add it to the project's dev dependencies
cd your-project
devloop init                 # writes devloop.toml, AGENTS.md, skills and adapters
devloop doctor --check       # exits 0 when nothing has drifted; run it in CI
```

`init` writes:

| File | What it is |
|---|---|
| `devloop.toml` | The only project-specific configuration |
| `AGENTS.md` | Managed blocks (rewritten by `devloop sync`) and the project's own sections (never touched) |
| `CLAUDE.md` | `@AGENTS.md`, so Claude Code loads the same rules |
| `.gemini/settings.json` | `context.fileName` including `AGENTS.md`, for Gemini CLI |
| `.claude/skills/`, `.agents/skills/`, `.github/skills/` | Rendered skills: the fewest folders that serve every supported tool |
| `.devloop/manifest.json` | The rendered files, so sync never touches a file it did not write |

Where each tool reads, and the documentation behind it: [docs/tool-matrix.md](docs/tool-matrix.md).

## Commands

- `devloop init`: write `devloop.toml` if absent, then sync.
- `devloop sync`: re-render `AGENTS.md` managed blocks, skills and adapters from their
  sources. Edit the source (a skill folder, `devloop.toml`), never a rendered copy.
- `devloop doctor [--check]`: report a bad config, a version mismatch, missing
  `AGENTS.md` sections, a tool with no skills folder it reads, and any drift. `--check`
  exits 1 on any problem.

Project skills go in `devloop/skills/<name>/SKILL.md` (frontmatter `name` and
`description` only) and are rendered alongside the framework's.

## Status

Phase 1: configuration, `init`, `sync` and `doctor`. Hygiene checks, the review core,
labels, lessons, metrics and the council follow in later phases.

## Licence

Apache-2.0. See [LICENSE](LICENSE).

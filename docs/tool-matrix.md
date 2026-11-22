# Per-tool setup matrix

Where each supported coding agent reads its instructions and its skills, and what
`devloop sync` writes for it. Checked against each tool's own documentation on
2026-11-22; the same sources are recorded in `src/devloop/tools.py`. Support changes
fast: re-check on every release.

| Tool | Reads `AGENTS.md` | Adapter written | Skills folder devloop relies on |
|---|---|---|---|
| Claude Code | Only when no `CLAUDE.md` exists | `CLAUDE.md` containing `@AGENTS.md` | `.claude/skills` |
| Codex | Yes | none | `.agents/skills` |
| Gemini CLI | Only when `context.fileName` names it | `.gemini/settings.json` with `context.fileName` | `.agents/skills` (alias of `.gemini/skills`) |
| Antigravity CLI | Yes (and `GEMINI.md`) | none | `.agents/skills` |
| Cursor | Yes | none | `.agents/skills` (also reads `.cursor/skills`, `.claude/skills`) |
| GitHub Copilot | Yes (cloud agent, CLI, code review) | none | `.github/skills` |

So the default `skills_targets` are `.claude/skills`, `.agents/skills` and
`.github/skills`: the fewest folders that serve every supported tool.

## Notes

- **Claude Code.** A `CLAUDE.md` that imports `@AGENTS.md` loads it in every case, and
  the docs say it is never read twice. `.agents/skills` is not a Claude Code skills folder.
- **Gemini CLI.** The settings key is nested, `context.fileName`, not the older top-level
  `contextFileName`; it may be a list. devloop adds `AGENTS.md` and keeps the other names
  (default `GEMINI.md`) and every other setting. Gemini CLI was replaced by Antigravity
  CLI on 2026-06-18.
- **Antigravity CLI** (`agy`, tool name `antigravity`). Reads `AGENTS.md` and `GEMINI.md`
  natively in the workspace and each parent up to the repository root, so it needs no
  adapter. Workspace skills come only from `.agents/skills`: a project's `.gemini/skills`
  must be renamed. Its docs do not say whether it honours `.gemini/settings.json`, which
  devloop writes for Gemini CLI only.
- **Copilot.** The cloud agent and CLI also read `.agents/skills` and `.claude/skills`,
  but Copilot in VS Code reads only `.github/skills`, so devloop requires that folder.
- **Duplicates.** Cursor and the Copilot cloud agent read more than one of the default
  folders, so they may list a skill once per folder. The copies are identical and
  `devloop doctor --check` fails if they ever differ.
- **Frontmatter.** Only `name` and `description`, with the agentskills.io rules for
  `name`: 1-64 lower-case letters, digits and single hyphens, matching the folder.
  Copilot in VS Code silently skips a skill that breaks them.

## Sources

- Claude Code: <https://code.claude.com/docs/en/memory>, <https://code.claude.com/docs/en/skills>
- Codex: <https://learn.chatgpt.com/docs/agent-configuration/agents-md>,
  <https://learn.chatgpt.com/docs/build-skills>
- Gemini CLI: <https://geminicli.com/docs/cli/gemini-md/>,
  <https://geminicli.com/docs/cli/skills/>,
  <https://antigravity.google/docs/cli/gcli-migration.md>
- Antigravity CLI: <https://antigravity.google/docs/rules>,
  <https://antigravity.google/docs/skills>,
  <https://antigravity.google/docs/cli/gcli-migration.md>
- Cursor: <https://cursor.com/docs/context/rules>, <https://cursor.com/docs/context/skills>
- GitHub Copilot: <https://docs.github.com/en/copilot/reference/custom-instructions-support>,
  <https://docs.github.com/en/copilot/concepts/agents/about-agent-skills>,
  <https://code.visualstudio.com/docs/copilot/customization/agent-skills>
- Agent Skills specification: <https://agentskills.io/specification>

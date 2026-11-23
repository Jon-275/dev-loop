# dev-loop

A standard review and delivery loop across coding agents. The rules every agent follows
come from one source, and git hooks and CI enforce them, so no agent can skip them.

It works with Claude Code, Codex, Gemini CLI, Antigravity CLI, Cursor and Copilot.

## The four layers

1. **Instructions.** `AGENTS.md` and the skills say how work is done here. `devloop sync`
   copies them to where each tool reads them ([tool matrix](tool-matrix.md)).
2. **Gates.** Git hooks and CI check what must hold: size, changelog, doctor, model
   reviews, labelled replies. A failing gate is fixed, never bypassed.
3. **Feedback.** Labelled replies to review findings become the review's examples and the
   lessons every agent reads first.
4. **Measurement.** A weekly report shows the loop's figures, per authoring tool.

## Five-minute adoption

```bash
uv tool install git+https://github.com/Jon-275/dev-loop@v0.5.0
cd your-project
devloop init              # write devloop.toml, AGENTS.md, skills and adapters
devloop doctor --check    # nothing has drifted; run this in CI too
```

Then call the shared workflows from CI and make their statuses required:
[adoption](adoption.md).

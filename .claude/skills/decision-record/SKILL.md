---
name: decision-record
description: Record a product or framework decision the owner made in docs/decisions/, with what was decided, why, how to apply it and its scope, so code and reviews follow it. Use when the owner settles a question that binds future work; not for routine implementation notes or for a decision the owner has not made.
---

# Record a decision

A decision in `docs/decisions/` binds code and reviews: a change that contradicts one is a
review finding unless it records a new decision. Only the owner makes a decision; an agent
records it.

## Steps

1. **Check it is a decision.** It came from the owner, it binds later work, and it is not
   already recorded. Search `docs/decisions/` first; if one covers it, update that file.
2. **Name the file** with the next free number and a short slug:
   `docs/decisions/NNNN-<slug>.md`.
3. **Write these sections**, briefly:
   - **Decision:** what was decided, in one or two sentences.
   - **Why:** the reason the owner gave, and what it rules out.
   - **How to apply:** what code, config or review must do because of it.
   - **Scope:** what it covers and what it does not; when it should be revisited.
   - **Date and source:** when, and where the owner said it (a PR, an issue).
4. **Link it** from the code or docs it binds, and from `AGENTS.md` if every agent must
   know it.
5. Open it as its own PR, or with the change that applies it.

## When not to use

- A choice the owner has not made: ask them, do not record your recommendation as one.
- Implementation notes or a how-to: they belong in the code's docs.

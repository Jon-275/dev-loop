---
name: council-design
description: Design or revise a project's council (scan the project for signals, interview the owner, pick and specialise role cards from devloop's library, group them into reviewers within budget, prove the council on known defects, record it as a decision). Use when a project adopts devloop or at the quarterly council review; not for reviewing a change (council-review).
---

# Design a council

A payments service, a mobile app and a data pipeline need different reviewers. The
council is a product decision the owner makes; this skill drafts it.

## Steps

1. **Scan for signals** and note each one with the file that shows it:
   - languages, frameworks and dependencies (for example `scikit-learn` points to ML,
     `stripe` to payments, `react` to UX, migrations to data engineering, Terraform or
     Helm to infrastructure, an LLM SDK to LLM applications);
   - external integrations and webhooks;
   - fields that look like personal or regulated data;
   - `docs/decisions/`, the README, `[tiers]` in `devloop.toml`, and the lessons page.
2. **Interview the owner** with a few questions: who uses the product; which failure costs
   most (wrong money, data loss, downtime, a compliance breach, a bad user experience);
   which regulations apply; which figures the product reports.
3. **Pick roles.** `devloop council library` lists the library. Every council starts
   with the always-present roles; add each domain pack a signal or answer calls for, and
   say which signal or answer chose it. Write a project-specific card for a risk no pack
   covers.
4. **Add and specialise.** `devloop council add <role>` copies a card into the council
   folder. Then edit each card for this project: `summoned_by` globs for its real paths,
   `tiers`, checks and severity anchors in the project's terms, proof methods that run
   here, the decision files it enforces.
5. **Group into reviewers.** Roles that share a `reviewer` run in one pass. Fit
   `council.max_reviewers` for each tier; `devloop council check` fails a council over
   budget, a glob that matches no file and a decision file that does not exist.
6. **Prove it** before relying on it: take past accepted findings (labelled threads,
   council notes), run `council-review` on each PR's head before its fix, and measure
   recall (known defects found) and precision (findings that are real). A role whose area
   had findings but catches none is under-specified. The owner sets the bar, for example
   70% of replayed P0 and P1 defects.
7. **Record it** in one PR: the cards, and a decision record with each role and why it
   was chosen, named the way `decision-record` names them
   (`docs/decisions/NNNN-council.md`). Run `devloop doctor --check`.

## Quarterly review

Add a role where accepted findings cluster in an area no role covers; merge or retire a
role with almost no unique accepted findings; re-run step 6 after any change. Fill each
card's "Known false positives" from its rejected findings.

## When not to use

- Reviewing a change: use `council-review`.
- Designing a change with the council's roles: use `council-build`.
- Never adopt a drafted council without the owner's approval; it is their decision.

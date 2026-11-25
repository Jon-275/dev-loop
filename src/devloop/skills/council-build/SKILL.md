---
name: council-build
description: Design a change with the project's council before writing it (plan who the change summons from the paths it will touch, give each reviewer the brief, take each role's design for its own area, fold them into one design with its invariants and tests, then build). Use before writing a tier-1 or tier-2 change; not for a tier-3 change, for reviewing a finished change (council-review) or for designing the council itself (council-design).
---

# Build with the council

A council that only reviews finds a design problem after the code exists, and the fix is
a rewrite. The same roles find it in minutes when they are asked first. This skill puts
the council's roles on the design of a change; `council-review` still judges the result.

## Steps

1. **Write the brief**, in the notes folder, in a few lines each:
   - what the change is for and who reads or uses its result;
   - the paths it will touch;
   - what is fixed: the owner's decisions, the decision files that bind the area, the
     rules in `AGENTS.md`;
   - the questions you cannot answer yet.
2. **Plan who is summoned.** Run `devloop council plan --paths <the paths it will touch>`.
   It prints the tier and each reviewer with its roles. Tier 3, or no roles: stop here
   and build with `pr-workflow`.
3. **Ask each reviewer for its design**, one pass per reviewer, in parallel where your
   agent can start independent workers. Give each the brief and its role cards, and ask
   for, in its own area:
   - what must hold when the change is done (invariants), and what must never happen;
   - what the user, operator or reader sees, in the words they will see;
   - the decisions that bind it, and any it would contradict;
   - the tests that would prove each invariant, with inputs;
   - the risks it accepts, and why.

   Ask for a design, not for agreement: do not hand a reviewer your own design to
   confirm. A reviewer that is shown an answer tends to return it.
4. **Fold the designs into one.** Where two roles disagree, choose, and write down what
   you took, what you left and why. A choice that belongs to the owner (a figure that
   changes, a rule that is relaxed, a new exception to a decision) goes to the owner
   before you build; record the answer with `decision-record`.
5. **Put the design in the pull request description**: the invariants, what the reader
   sees, what is not done. For stateful code, continue with `stateful-change`: the
   invariants become the end-state test.
6. **Build** with `pr-workflow`, tests first: each invariant gets the test its role named.
7. **Review as usual.** Run `council-review` before merge, with reviewers that did not
   see the design discussion. The design round does not replace it and does not count as
   a verdict.

## Keep it proportionate

- One round is the default. Go back to a role only with a specific question.
- A small tier-2 change needs its summoned reviewers and a few lines each, not a document.
- Count the round's cost with the review's: a reviewer per pass, like `council-review`.

## When not to use

- A tier-3 change (docs, tests only), or a change that summons no role.
- Reviewing a finished change: use `council-review`.
- Designing or changing the council itself: use `council-design`.

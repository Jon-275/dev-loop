---
name: council-review
description: Review a pull request or branch as the project's council (the role cards in the council folder, grouped by reviewer), with every serious finding proven by a command that was run. Use before merging a tier-1 or tier-2 change, or for a check of only the commits added since an earlier review; not for writing the fix or designing the council (council-design).
---

# Council review

The council is how a project decides a change is safe to merge. Its value is evidence: a
finding the author can rerun, not an opinion. The roles come from the project's role
cards (`council.roles_dir` in `devloop.toml`, default `devloop/council/`), not from this
skill.

## Plan the review

1. Run `devloop council plan --base <base>` (default base `origin/main`). It prints the
   change's tier and each reviewer with the roles it runs. No roles means no council round.
2. Read each summoned role's card: its mandate, checks, severity anchors, proof methods,
   the decisions it enforces, what is out of scope and its known false positives.
3. Read the decision files the cards name. A change that contradicts a decision is a
   finding, unless the PR records a new decision.
4. **Full review or delta.** A full review reads the whole change. A delta check reads only
   the commits since the review that judged it safe: name that base commit, and re-run the
   earlier proofs so a fix has not broken what held.

## Run it

- One reviewer per pass: each reviewer reviews the change against all of its roles at
  once. Run the reviewers in parallel where your agent can start independent workers, one
  after another where it cannot; give each the diff, its cards and the evidence rules.
- **Prove every P0 and P1** with a command or script you ran, and record its output.
  Anything not run is labelled a hypothesis.
- **Stateful code:** probe the end state after sequences of operations and faults, under
  every id a record is known by. **Figures:** reproduce the number from its inputs.
- Keep probes out of the repository: in the notes folder (`REVIEW_NOTES_DIR`) or a
  scratch folder, and name them in the report.
- Drop a finding a card lists as a known false positive, unless this case differs; say why.

## Severity

- **P0:** wrong numbers or decisions in production without any error, or data loss.
- **P1:** a likely production failure, a compliance blocker, or a significant flaw in a
  reported figure.
- **P2:** robustness or maintainability. **P3:** polish.

A real problem outside the PR's concern is reported as out of scope, with the reason.

## Report

Start the report file with this frontmatter, which `devloop council report` judges for
the `Council review` status, then the verdict, **SAFE** or **not safe**, and the count per
severity:

```markdown
---
pr: 12
head: "<the full commit you reviewed: git rev-parse HEAD>"
tier: 1
reviewers: ["core", "security"]
verdict: "safe"
findings: {"P0": 0, "P1": 0, "P2": 2, "P3": 1}
---
```

`verdict` is `"safe"` or `"not safe"`, and it is the verdict that counts: keep the prose
in step with it. `findings` gives a count for every severity, zero included; `reviewers`
lists every reviewer that ran. A report
passes only for that exact head, so after a new push run a delta check and write a new
report. Run `devloop council report --report <file>` to see the verdict. Then each
finding: location (`file:line`), proof (command and output), impact, expected fix; in
severity order. Then what was checked and held, so the next delta check knows what to
re-run. Write the full notes to `review-pr<number>.md` in the notes folder, where
`devloop lessons --notes <folder>` counts them. Label the author's replies to council
findings `accepted:` or `rejected:` like any review thread.

## When not to use

- Writing the fix: the council does not edit the code under review.
- A tier-3 change (docs, tests only): no council round.
- Designing or changing the council itself: use `council-design`.

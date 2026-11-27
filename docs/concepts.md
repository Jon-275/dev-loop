# Concepts

## The loop, enforced in CI

An agent reads `AGENTS.md` and the skills, writes a change on its own branch, reviews it
locally, and opens a pull request. From there CI decides, not the agent:

1. **Hygiene** (`pr-hygiene.yml`): `devloop doctor --check`, the size limit, the changelog
   fragment, and whether only documentation changed.
2. **Model reviews** (`review.yml`): each reviewer posts its blocking findings as threads
   and a commit status that fails closed.
3. **Review labels** (`labels.yml`): every finding thread needs a member's reply starting
   with a label word, and the status waits for the reviews to finish.
4. **Merge gate** (`gate.yml`, optional): one status that stands for the reviews and the
   labels, so branch protection requires one name. A pull request of documents only waits
   for no reviewer ([merge gate](gate.md)).
5. **Merge** only when the required statuses pass and every thread is resolved.

Instructions explain the rules; the gates hold them. See
[decision 0001](decisions/0001-enforce-in-git-and-ci.md).

## Risk tiers

Each changed file has a tier; the pull request takes its riskiest file's tier.

- **Tier 1** files match a `[tiers]` `1` glob: money, data writes, security, reported
  figures. They get every reviewer and the full council.
- **Tier 3** files match a `3` glob, such as docs. They get the fewest reviews.
- **Tier 2** is everything else.

`review.providers` routes reviewers per tier, and `council.max_reviewers` caps the council
per tier. The same tier rule drives the review and `devloop council plan`.

## Labels and lessons

A reply on a finding thread starts with a label word, `accepted:` (the finding was right)
or `rejected:` (it was wrong), then says what changed or why not.

- `devloop feedback` collects labelled, resolved threads into `review.feedback`. Each
  reviewer's prompt learns from it, and a finding rejected three times is suppressed
  (never a P1).
- `devloop lessons` ranks the bug classes accepted findings fall into and writes the top
  rules into `AGENTS.md`, so every agent reads them before changing code.

A thread without a label is lost to both, which is why the `Review labels` status fails
until every finding has one.

## Metrics, and what "aligned" means

`devloop metrics` measures the pull requests merged in a window: time to merge, size,
threads by reviewer and severity, labels, precision, unique accepted findings and model
tokens. Nothing is dropped without being named.

When a repository opts in to the `Agent:` commit trailer, metrics also report figures
per authoring tool. The tools are **aligned** when their figures
are alike: the same rules produce similar sizes, review rounds and rejected shares. A tool
that is an outlier is not following the rules, and the fix is in its instructions or
adapter, not in a gate.

## The council

For tier-1 and tier-2 changes, a council of roles reviews with proof: every P0 and P1
finding comes with a command that was run. Each project designs its own council from
devloop's role library ([the council](council.md)).

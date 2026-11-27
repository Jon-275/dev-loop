# Cost model

## What a push costs

Every push to a pull request re-runs CI and every routed reviewer. Per push:

- **CI:** the caller's code checks plus `pr-hygiene.yml`, a few runner minutes.
- **Each model reviewer:** one model call (more for a pull request split into parts). The
  input is the lens, the rules, the feedback examples, the diff and up to 15 changed
  files; the output is the findings. `devloop metrics` reports the tokens each reviewer
  used, so the cost is measured, not estimated.
- **The council,** for tier 1 and tier 2 changes: one pass per summoned reviewer, run by
  the author's agent.

So the number of pushes multiplies everything. Large pull requests cost most: they take
more rounds, and each round re-reads more.

## The levers

| Lever | Setting | Effect |
|---|---|---|
| One push per review round | `address-review` skill | the fewest reviewer runs per pull request |
| Small pull requests | `pr.max_lines` | fewer rounds, and less text per review |
| Route reviewers by tier | `review.providers` | fewer reviewers on low-risk changes |
| High effort only where it matters | `review.high_effort` | cheaper reviews of ordinary changes |
| A cap on reviews per pull request | `review.max_rounds` | after that many reviews by a reviewer, no more model calls until a maintainer asks |
| Incremental reviews | always on | after a completed review, only the pull request's own changes since then are reviewed |
| Skip code checks on docs | `devloop changes` | a docs-only pull request runs only the tests that read docs |
| Review locally first | `review-locally` skill | findings fixed before a billed round on the PR |
| Council budget | `council.max_reviewers` | caps council passes per tier |

Read the weekly `devloop metrics` report to see which reviewer finds what for its tokens:
a reviewer with few unique accepted findings is the first to route away from low tiers.

## No spend budget

devloop has no daily or monthly budget. It does not know prices, and it does not add up
what a project has spent. The control for total spend is the provider's own spend cap:
set one on each reviewer's API key.

`review.max_rounds` bounds one thing: how many reviews one reviewer posts on one pull
request ([a cap on reviews](review.md#a-cap-on-reviews)). It does not bound:

- the size of a review: a large diff costs more, and a review split into parts is
  several model calls but one round;
- the number of pull requests, or of reviewers;
- runs that fail before they post, such as a model error: they post no review, so they
  are not counted, though the provider may bill their tokens;
- reviews a maintainer asks for with the `review-again` label.

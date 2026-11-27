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

## GitHub API reads per run

`devloop labels` and `devloop gate` read GitHub each time they run, and a reply on a
review thread starts both. The workflow token (`GITHUB_TOKEN`) may make 1,000 REST
requests per repository per hour. GraphQL calls count against a separate limit. Tests
pin the counts below, so a change that adds a request fails a test.

One evaluation of a typical pull request (from a branch of the repository, with under
100 threads and under 100 statuses):

| Run | REST reads | GraphQL reads | Status writes | Requests |
|---|---|---|---|---|
| `devloop labels` | 3 | 2 | 2 | 7 |
| `devloop gate`, light lane, one check | 10 | 0 | 2 | 12 |
| `devloop gate`, strict lane, two statuses and one check | 10 | 0 | 2 | 12 |

What moves the counts:

- The number of threads does not: threads come 100 to a page. Each further page is one
  more GraphQL read per pass.
- `devloop labels`: each name in `labels.wait_for_checks` adds 2 reads.
- `devloop gate`: one listing answers every status. Each check in `gate.checks` is 2 of
  the reads above. With no check the gate makes 8 reads.
- A fork's pull request adds 1 read (the status guard).
- A verdict that changed while it was being posted adds 1 write.
- `gate.yml` itself reads the head once before devloop starts: 1 more REST read per run.

Each run reads the reviews twice, before and after it posts. The second read is there
on purpose: a reply or a review can land while the run is posting. No read was removed.

**A busy pull request.** A person's reply can start two workflow runs: GitHub sends
the comment event and the review event that wraps a single reply. Each run is one
`devloop labels` and one `devloop gate`:

- REST requests per run: 5 (labels) + 12 (gate) + 1 (`gate.yml`) = 18.
- Per reply, at two runs: 36 REST requests and 4 GraphQL reads.
- 20 replies in an hour: 20 x 36 = 720 of the 1,000 REST requests.

That leaves 280 for everything else in the hour: CI, the reviews and other pull
requests. To stay under the limit, answer the threads of a round together and submit
them as one review: one review with ten replies starts the checks far fewer times than
ten single replies. `gate.yml` also runs one evaluation of a pull request at a time and
keeps only the newest waiting run, so a burst of replies costs less than the sum above.

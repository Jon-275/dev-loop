# Model reviews

`devloop review --provider NAME` reviews a pull request with a model and posts each
finding as an inline thread. `main` merges only when every thread is resolved, so each
finding is fixed or answered.

## Reviewers

Built in: `gemini`, `reflection` (an OpenAI-compatible API) and `anthropic`
(`claude-opus-5-5`, through the official SDK: install `dev-loop[anthropic]`). Any other
OpenAI-compatible API is a table of its own. Its status is the name capitalised, plus
"review" (`Mine review`):

```toml
[review.reviewers.mine]
kind = "openai"
model = "my-model"
base_url = "https://llm.example.com/v1"
key = "MINE_API_KEY"
dimensions = { D1 = "Revenue alignment" }   # optional tags each finding must carry
summary = "summary: two or three sentences on what changed; do not say it is safe to merge."
```

A reviewer's lens, the opening of its prompt, is `<review.lenses_dir>/<name>.md` when
that file exists, and a general code-review lens otherwise.

## What blocks a merge

- `review.blocking_severities` (default P1 and P2) get threads. Other findings are listed
  in the summary only.
- `[tiers]` gives a pull request the tier of its riskiest file, and `review.providers`
  says which reviewers each tier gets. A reviewer that a tier does not route to passes,
  and its status says so. With no `review.providers`, every reviewer reviews every tier.
- `review.files` names the file types a reviewer reads. A pull request that changes none
  of them passes without a request.

Nothing is hidden: the summary names every finding that got no thread and why (an
earlier thread, another reviewer's thread, review feedback, below the blocking
severities), every concern that is not a finding, and every file that was not reviewed.
An incomplete review fails its status.

## Anthropic

The `anthropic` reviewer needs the SDK, so install devloop with its extra, for example
`devloop: "dev-loop[anthropic] @ git+https://github.com/Jon-275/dev-loop@<tag>"` in
`review.yml`'s caller. Requests stream, ask for JSON in the review schema at the run's
effort, and use the API's server-side fallback (`fallbacks: "default"`): a request the
model declines is re-run on a fallback model in the same call. Set `model` in
`[review.reviewers.anthropic]` to use another Claude model.

## In CI

Call [`review.yml`](../.github/workflows/review.yml) from a workflow on
`pull_request_target`, one job per reviewer; its header shows the job. The workflow
installs devloop and reads `devloop.toml`, the lenses and `review.feedback` from the base
branch's checkout. It reads the pull request only through Git, so the pull request's own
code never runs while the key is present.

After a completed review, only the pull request's own changes since then are reviewed,
and a push that only merges the base branch is not reviewed again.

## Locally

```bash
GEMINI_API_KEY=... devloop review --provider gemini --local --base origin/main
```

It prints the summary and each finding and posts nothing.

## Token usage

Each summary ends with the requests and tokens the review used, and a hidden
`<!-- model-usage: ... -->` marker with the same counts and the run's id.

## Replaying gtm_toolkit's reviews

`scripts/replay.py` runs one gtm_toolkit pull request through gtm_toolkit's own
reviewers and devloop's, with nothing posted. The prompts must be identical byte for
byte; the findings are matched by severity, file and line within five. With `--fake` it
compares prompts only and calls no model. In CI, run the "Replay a gtm_toolkit pull
request" workflow by hand; it needs the `GTM_READ_TOKEN` secret and reports counts and
prompt hashes only, since this repository's logs are public.

## Labels and feedback

Reply to every finding thread in one line that starts with a label word
(`labels.prefixes`, by default `accepted:` when the finding was right and `rejected:` when
it was wrong), then resolve it. Only a labelled reply from a repository owner, member or
collaborator counts.

- `devloop labels` posts the "Review labels" status: it fails while any finding thread,
  resolved or not, has no labelled reply, and stays pending until every review in
  `labels.wait_for` (statuses) and `labels.wait_for_checks` (check runs) has finished.
  Call [`labels.yml`](../.github/workflows/labels.yml) after the review jobs and on
  review replies.
- `devloop feedback` collects the labelled threads of recently merged pull requests into
  `review.feedback`. The reviewers learn from it: recent decisions become prompt
  examples, and a finding rejected three times is suppressed (listed in the summary, never
  a thread; a P1 never).

## Metrics

`devloop metrics --days 7` measures the review loop over the pull requests merged in the
window: time to merge, size against `pr.max_lines`, finding threads by reviewer and
severity, labels and precision, model tokens, and every figure **per authoring tool**. A
pull request's tool comes from its commits' `Agent: <tool>/<model>` trailers, which the
`commit-msg` hook adds when `DEVLOOP_AGENT` is set (`devloop agent --install`). Call
[`metrics.yml`](../.github/workflows/metrics.yml) from a weekly workflow.

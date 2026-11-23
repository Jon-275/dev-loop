# Model reviews

`devloop review --provider NAME` reviews a pull request with a model and posts each
finding as an inline thread. `main` merges only when every thread is resolved, so each
finding is fixed or answered.

## Reviewers

Built in: `gemini` and `reflection` (an OpenAI-compatible API). Any other
OpenAI-compatible API is a table of its own. Its status is the name capitalised, plus
"review" (`Mine review`):

```toml
[review.reviewers.mine]
kind = "openai"
model = "my-model"
base_url = "https://llm.example.com/v1"
key = "MINE_API_KEY"
dimensions = { D1 = "Revenue alignment" }   # optional tags each finding must carry
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

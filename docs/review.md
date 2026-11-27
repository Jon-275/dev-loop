# Model reviews

`devloop review --provider NAME` reviews a pull request with a model and posts each
blocking finding as an inline thread. With conversation resolution required on the
default branch, a pull request merges only when every thread is resolved, so each
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
  of them passes without a request. Workbook projects can include `*.bas` and
  `*.fragment` alongside their other source patterns to cover checked-in macro source.
  The configured list replaces the defaults. Verify generated workbook files through
  the project's artifact checks.

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

Call [`review.yml`](https://github.com/Jon-275/dev-loop/blob/main/.github/workflows/review.yml) from a workflow on
`pull_request_target`, one job per reviewer; its header shows the job. The workflow
installs devloop and reads `devloop.toml`, the lenses and `review.feedback` from the base
branch's checkout. It reads the pull request only through Git, so the pull request's own
code never runs while the key is present.

The shared workflows' own GitHub calls (reading the head, writing a status, removing the
approval label) are tried up to three times: again after 2 seconds, then 4, on HTTP 429,
a 5xx or a lost connection. Any other answer, such as 403, 404 or 422, is final at once.
A call that still fails after the third try fails its step as before.

After a completed review, only the pull request's own changes since then are reviewed,
and a push that only merges the base branch is not reviewed again.

### Outside pull requests

A review spends the project's key and sends the pull request's text to the reviewer. By
default both are kept for people the project trusts (`gate: members`). A pull request
from anyone else waits until a maintainer has looked at it and added a label.

```yaml
on:
  pull_request_target:
    types: [opened, synchronize, reopened, ready_for_review, labeled]

jobs:
  unapprove:
    uses: Jon-275/dev-loop/.github/workflows/unapprove.yml@<tag>
    permissions: { pull-requests: write }

  gemini:
    uses: Jon-275/dev-loop/.github/workflows/review.yml@<tag>
    permissions: { contents: read, pull-requests: write, statuses: write }
    with:
      provider: gemini
      devloop: "git+https://github.com/Jon-275/dev-loop@<tag>"
    secrets: { api_key: "${{ secrets.GEMINI_API_KEY }}" }
```

| Author | What happens |
|---|---|
| The owner, a member or a collaborator | Reviewed on every push |
| A login in `trusted_bots` (default `dependabot[bot]`) | The same |
| Anyone else | The status is pending, "Waiting for a maintainer to add the ai-review-approved label". Adding the label reviews the head it was added to, once |

Three things to know:

- **Approval covers one head.** A push after the label is a new head and waits again.
  `unapprove.yml` removes the label on every push, so a maintainer can add it again.
  Look at the new commits first.
- **Trigger on `labeled`.** Without it the label starts nothing. Adding any other label
  changes nothing: it is not reviewed again and no status is touched. If the calling
  workflow cancels older runs of the same pull request, leave such label events out of
  that group, or adding a label can cancel a review in progress.
- **Who counts as a member** is GitHub's `author_association` on the pull request. A
  member of an organisation whose membership is private is not reported as one, and
  waits like anyone else.

**What an approved outside pull request may cost** is bounded by
`review.outside_max_chars` (150,000 characters of diff and file contents by default).
Over it, nothing is sent and the status says so; review it by hand, ask for a smaller
pull request, or raise the limit.

The decision is made in a job that holds no key and reads only what GitHub sets on the
event. `approval_label` and `trusted_bots` rename the label and the trusted logins; pass
the same `approval_label` to `unapprove.yml`.

### A cap on reviews

Every push re-runs every reviewer. `review.max_rounds` caps how many reviews one reviewer
gives one pull request. The default is `0`: no cap, and nothing changes.

```toml
[review]
max_rounds = 5
```

- **What counts as a round.** One review the workflow account (`github-actions[bot]`)
  posted on the pull request with that reviewer's summary. A run that posted no review
  does not count: not routed, nothing new to review, or a run that failed.
- **At the cap** the next run sends nothing to the model. The reviewer's status is
  `failure` and reads "Not reviewed: cap of 5 reviews reached (5 done); a maintainer can
  add the review-again label". It is never a pass and never a silent skip: the head was
  not reviewed.
- **One more review.** A maintainer adds the `review-again` label. That `labeled` event
  reviews the head once, past the cap. For another, remove the label and add it again.
  Adding a label needs triage rights, so an author from outside cannot do it.
- **Outside pull requests** stay behind the approval gate. There the `review-again`
  label alone starts nothing. Add it, then add the approval label: the approved review
  passes the cap while `review-again` is on the pull request.
- **The count cannot be reset by the author.** It is per pull request, not per head, so
  a force push changes nothing. Only the workflow account's reviews count, so text an
  author writes neither adds a round nor removes one. An author cannot delete or edit
  the workflow account's reviews. A new pull request starts at zero; for an outside
  author it waits for approval like any other.

Rename the label with `review.yml`'s `again_label` input. The caller must trigger on
`labeled`, as it does for the approval label.

**To review everyone at once**, pass `gate: everyone`. That suits a private project
whose authors are all trusted. On a public project it lets any account spend the key.
Any other `gate` value, an empty one included, fails the run and reviews nothing.

### Secrets are not sent

Before the first request, the diff and the file contents a review is about to send are
checked for likely secrets: a provider's own key format (AWS, GitHub, Google, Slack,
Anthropic, OpenAI, Stripe) or a private-key header. If one is found nothing is sent to
any reviewer, the check fails, and the run names the file, the line and the kind of key,
never the text. Remove the key, and rotate it if it is real: it is already in the pull
request's history. A file whose test values only look like keys goes in
`review.secret_allow`; `secret_scan = false` turns the check off.

The patterns are exact formats, so a secret with no recognisable shape (a password, a
random token) is not caught. This is a last check, not a replacement for secret scanning
on the repository.

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

- The `Review labels run` job is green whenever the check ran and posted the `Review labels` status,
  pass or fail; a red job means the check itself could not run and should be re-run.
- `devloop labels` posts the "Review labels" status: it fails while any finding thread,
  resolved or not, has no labelled reply, and stays pending until every review in
  `labels.wait_for` (statuses) and `labels.wait_for_checks` (check runs) has finished.
  Call [`labels.yml`](https://github.com/Jon-275/dev-loop/blob/main/.github/workflows/labels.yml) after the review jobs and on
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
`commit-msg` hook adds when `agents.commit_attribution` is `true` and `DEVLOOP_AGENT`
is set. Install the optional hook with `devloop agent --install`. Call
[`metrics.yml`](https://github.com/Jon-275/dev-loop/blob/main/.github/workflows/metrics.yml) from a weekly workflow.

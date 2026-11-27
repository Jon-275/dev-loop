- **`review.max_rounds`: a cap on reviews per pull request.** Each push re-runs every
  reviewer, and nothing limited how often. With `max_rounds` set, once a reviewer has
  posted that many reviews on a pull request the next run sends nothing to the model and
  its status fails with "Not reviewed: cap of N reviews reached": never a pass, never a
  silent skip. A maintainer adds the `review-again` label (`review.yml`'s new
  `again_label` input) for one more review; on an outside pull request the approval
  label is still needed first. A round is a review the workflow account posted, so an
  author cannot forge or reset the count. The default, `0`, is no cap and changes
  nothing. `docs/cost.md` now says plainly that devloop has no spend budget.

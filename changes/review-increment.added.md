- **Review: incremental reviews.** After a completed review, `devloop.review.increment`
  plans a review of only the pull request's own changes since then, or none when the new
  commits only merge the base branch. A conflict resolution, a force push or a new base
  gets a full review, and the pull request's merge attributes are never used.

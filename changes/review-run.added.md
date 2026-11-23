- **Review: the run.** `devloop.review.run` routes a review by the pull request's tier
  (`[tiers]` and `review.providers`), asks the model, splits a diff too long for one reply
  into parts within the job's time budget, places each blocking finding as a thread and
  posts the summary. A reviewer a tier does not route to passes and says why.

- **Review: retries and split reviews.** `devloop.review.parts` retries a model request
  with backoff while it is briefly unavailable, and reviews a diff too long for one reply
  in parts within the job's time budget, naming every file it could not review.

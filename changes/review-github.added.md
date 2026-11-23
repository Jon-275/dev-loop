- **Review: GitHub calls and the summary.** `devloop.review.github` lists threads, finds the
  last completed review and posts reviews with the standard library only, failing on any
  refused listing; `devloop.review.body` writes the summary, which now also lists findings
  below `review.blocking_severities` (P3 by default) instead of opening threads for them.

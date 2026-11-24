- **`devloop council report` judges a council report.** The `council-review` skill's
  report now starts with frontmatter (pull request, head commit, tier, reviewers, verdict,
  findings per severity). The command judges it for the head: success only for a safe
  report on that exact commit, at the change's tier or riskier, from every summoned
  reviewer, with no P0 or P1; anything not judged is pending, never a pass. Posting it as
  the `Council review` status follows.

- **Anthropic reviewer.** `devloop review --provider anthropic` reviews with Claude
  (`claude-opus-5-5` by default) through the official SDK, installed as the optional extra
  `dev-loop[anthropic]`. Requests stream, ask for JSON in the review schema at the run's
  effort, and turn on the API's server-side refusal fallback.

- **`devloop review --provider NAME` and `review.yml`.** Runs a reviewer in CI, reading
  `devloop.toml`, lenses and feedback from the base branch's checkout (`--trusted`), or
  locally with `--local`, which prints the findings. The shared `review.yml` runs it from
  a caller on `pull_request_target`, and dev-loop reviews its own pull requests with it.

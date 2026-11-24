- **`devloop bench` runs the conformance benchmark.** It runs Claude Code headless on each
  task in a fresh copy of the fixture, under a time limit, and prints a scorecard that
  counts and names every failed check; the agent's transcript stays in a local log. Local
  only (decision 0006); the `release` skill runs it for devloop's own releases.

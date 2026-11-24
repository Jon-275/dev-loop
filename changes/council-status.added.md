- **The `Council review` status.** `devloop council report --post --pr <N>` posts a
  report's verdict on the pull request's head, read from GitHub. The shared `council.yml`
  workflow keeps it current on every push: pending while the head's tier needs a council,
  success where none is required, never overwriting a verdict already on that head. Tier 1
  can now require a council review whichever tool ran it.

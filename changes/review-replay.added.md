- **Replay a gtm_toolkit pull request.** `scripts/replay.py` and the hand-run
  `replay.yml` run one pull request through gtm_toolkit's reviewers and devloop's and
  compare them: prompts must be identical, findings are matched by severity, file and
  nearby line. `[review.reviewers.NAME] summary` sets a reviewer's summary instruction,
  and three or more dimension tags read as a range ("D1-D9"), so Reflection's prompt is
  gtm_toolkit's byte for byte.

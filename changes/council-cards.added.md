- **Council role cards.** One Markdown file per role in `council.roles_dir` (default
  `devloop/council/`) says when the role is summoned (`summoned_by` globs and `tiers`),
  which reviewer runs it, and its mandate, checks, severity anchors and proof methods.
  `devloop council plan` prints the roles and reviewers a change summons; `devloop council
  check` and `devloop doctor` report a bad card, a glob that matches no file, a decision
  file that does not exist, and a council with more reviewers for a tier than
  `council.max_reviewers` allows.

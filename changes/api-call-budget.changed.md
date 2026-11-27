- **The GitHub API requests of `devloop labels` and `devloop gate` are counted and pinned.**
  Each reply on a review thread starts both, and the workflow token may make 1,000 REST
  requests per repository per hour. Tests now pin the requests one evaluation makes (7
  for the labels check; 12 for the gate with two statuses and one check), so a change
  that adds a read fails a test. `docs/cost.md` lists the counts under "GitHub API reads
  per run", with the arithmetic for a busy pull request. No read was removed: each one
  is a guard or a second read that closes a race.

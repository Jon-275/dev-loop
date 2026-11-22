- **Shared workflows.** `pr-hygiene.yml` runs doctor, size, changelog and changes
  for any project, and `main-health.yml` keeps its "main is red" issue. dev-loop's own
  CI now calls both.

- **The weekly metrics can read commit statuses.** `metrics.yml` asks for
  `statuses: read`: `devloop metrics` reads each commit's review statuses, and without that
  permission GitHub refused the whole query (`FORBIDDEN`), so the job failed on its first
  scheduled run. Callers grant `statuses: read` too.

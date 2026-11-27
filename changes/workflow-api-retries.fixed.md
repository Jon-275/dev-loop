- **The shared workflows' own GitHub calls retry a transient failure.** One HTTP 502 from
  GitHub turned a job red, and in a step that writes a status it could leave a wrong
  status standing. Every `gh api` call in `review.yml`, `gate.yml`, `labels.yml` and
  `unapprove.yml` now goes through one small shell function, repeated in each step: up to
  three tries, waiting 2 then 4 seconds, on HTTP 429, a 5xx or a lost connection. A 403,
  404 or 422 is never retried. The status guard still fails closed when its read fails
  after the tries, and `unapprove.yml` still treats a 404 as success.

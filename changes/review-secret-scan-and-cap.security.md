- **A review refuses to send a likely secret.** Before the first request, the diff and
  the file contents a review would send are checked for provider key formats and
  private-key headers. On a match nothing is sent, the check fails, and the run names
  the file, the line and the kind of key, never the text. On by default; exempt test
  files with `review.secret_allow`, or set `secret_scan = false`.
- **An approved outside pull request has a size limit.** With `gate: members`, a pull
  request reviewed because a maintainer approved it may send at most
  `review.outside_max_chars` characters (150,000 by default); over it, nothing is sent.

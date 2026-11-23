- **`devloop metrics`.** Measures the review loop over the pull requests merged in a window:
  time to merge, size against `pr.max_lines`, finding threads by reviewer and severity,
  labels, precision, unique accepted findings, review app reviews and model tokens. Nothing
  is dropped silently: truncated pull requests and unmeasured tokens are named. Ported
  from gtm_toolkit.

- **A pull request cannot hide its code from a model reviewer.** Review diffs ignore the
  pull request's `.gitattributes` (`-diff` turned code into "Binary files differ"), and a
  changed file whose diff and contents both missed the prompt is listed as unreviewed, so
  the status fails instead of passing. The same gap is in gtm_toolkit's review core.

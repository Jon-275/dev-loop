- **Review: Git reads and the prompt.** `devloop.review.gitread` reads a pull request through
  Git only (no diff programs, no working-tree reads, no symlinks, no inherited `GIT_*`), and
  `devloop.review.prompt` builds the review prompt with every file name encoded.
  `review.high_effort` globs replace gtm_toolkit's fixed list of core-logic path hints.

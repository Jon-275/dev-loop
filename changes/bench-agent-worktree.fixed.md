- **The benchmark judges the checkout the agent worked in.** An agent that followed
  AGENTS.md and worked in a worktree under `.worktrees/` failed three checks, because only
  the main checkout was read; the one worktree with new commits on a branch is now judged.
  The fixture ignores `.worktrees/` and `review/replies.json` (the replies stand for
  comments on the pull request), and the replies are read from either checkout.

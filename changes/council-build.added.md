- **The council designs a risky change before it is written.** The new `council-build`
  skill puts the council's roles on a tier-1 or tier-2 change first: a short brief,
  `devloop council plan --paths` for the paths the change will touch, then each summoned
  reviewer gives a design for its own area (what must hold, what the reader sees, the
  decisions that bind it, the tests that prove it). The author folds them into one design
  and takes any choice that is the owner's to the owner before building. `AGENTS.md` gains
  the working rule, and `pr-workflow` the step. `council-review` still judges the result.

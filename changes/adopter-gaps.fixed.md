- **Hand-off notes stay out of git, and the council's record has one name.** `devloop sync`
  writes `.devloop/.gitignore` (`notes/`), so the notes `AGENTS.md` says are never
  committed are ignored, without editing the project's own `.gitignore`. The
  `council-design` skill and the council docs name the council's decision record the way
  `decision-record` names every record (`docs/decisions/NNNN-council.md`). Both found
  adopting devloop in a second repository.

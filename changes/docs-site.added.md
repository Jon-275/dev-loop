- **A documentation site.** `mkdocs build --strict` builds `docs/` with no warnings, and
  CI runs it on every pull request. New pages: concepts (the loop enforced in CI, risk
  tiers, labels and lessons, metrics and what "aligned" means), adoption (install, shared
  workflows, secrets, branch protection, agents' identity, the council), a configuration
  reference for every `devloop.toml` key, and the skills devloop ships. Tests keep the
  configuration page and the skills page exactly in line with the code.

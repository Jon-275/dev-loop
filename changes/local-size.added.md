- **`devloop size --base <ref>` measures a branch locally.** It counts the committed
  branch from where it left `<ref>`, with the same exclusions as the CI check, and exits 1
  over `pr.max_lines`. The `pr-workflow` and `split-stack` skills use it instead of
  `git diff --shortstat`, which counted lock and generated files; `devloop-setup` now says
  how to install devloop (Greptile on gtm_toolkit #274).

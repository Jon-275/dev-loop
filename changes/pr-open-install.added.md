- **`devloop pr-open --install`.** Installs the guard as git's pre-push hook, in
  `core.hooksPath` when set, behind pre-commit's hook when pre-commit owns pre-push, and
  never over another tool's hook.

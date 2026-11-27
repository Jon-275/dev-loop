- **A fork's pull request can no longer replace a status on a branch's head commit.** A
  commit status belongs to a commit, so a fork pull request whose head is also the head
  of a branch in the base repository could overwrite `Review labels`, a `<Provider> review`
  or `Council review` there, turning a failure green. The shared workflows and
  `devloop council report --post` now write no status for such a pull request and say why
  in a notice, and its review does not run. `labels.yml` no longer posts pending from the
  shell: `devloop labels` posts it after the check. The gate job of `review.yml` now needs
  `contents: read` for one read of the branches. See docs/security.md, "A status belongs
  to a commit".

# Changelog fragments

Each change adds one file here instead of editing `CHANGELOG.md`, so open pull requests
never edit the same lines.

Name the file `<slug>.<section>.md`:

- `<slug>`: lower-case letters, digits and hyphens, naming the change.
- `<section>`: `breaking`, `added`, `changed`, `fixed` or `security`. It picks the
  heading under `## [Unreleased]`.

The file holds the entry as it should read in the changelog:

```markdown
- **Short summary.** What changed and why it matters.
```

Commands:

- `devloop changelog check`: validate every fragment.
- `devloop changelog build --dry-run`: print the changelog with the fragments added.
- `devloop changelog build`: add the fragments to `CHANGELOG.md` and delete them.

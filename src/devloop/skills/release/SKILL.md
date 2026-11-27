---
name: release
description: Release a new version (bump the version, build the changelog from its fragments, open and merge the release PR, tag the merge commit and publish the release). Use when the owner asks for a release; not for adding a changelog entry to a change (pr-workflow).
---

# Release

A release is its own small PR, merged like any other, then a tag on its merge commit
and a published release.
For a model or generated report distributed with matching guides or previews, also
follow [the artifact bundle procedure](references/artifact-bundles.md). It keeps the
prior site active until the new downloads have been verified.

## Steps

1. **Pick the version** with the owner: a new feature is a minor bump, a fix a patch, a
   breaking change a major (with an upgrade note).
2. **Branch** from the latest default branch: `release/v<version>` in its own worktree.
3. **Bump the version** everywhere the project records it (the package metadata, the
   version the package itself reports, such as `__version__`, the lockfile entry, any install line in the README, and `framework.version` in
   `devloop.toml` when this project is devloop itself).
4. **Build the changelog:** `devloop changelog check`, then `devloop changelog build`.
   Move the new entries under a `## <version> - <date>` heading and leave the empty
   `[Unreleased]` headings in place.
5. **Run every gate**, including `devloop doctor --check`, then open the PR with
   `pr-workflow` and wait for its checks. When this project is devloop itself, also run
   `devloop bench` locally and put its scorecard in the PR description.
6. **Merge.** Where the project has a release workflow (in devloop,
   `.github/workflows/release.yml`), it tags the merge commit `v<version>` and publishes
   the release with that version's changelog section as the notes. Do not tag by hand
   first: the workflow does nothing for a version whose tag exists.
7. **Check** the workflow's run and the default branch's CI on the merge commit, then
   `gh release view v<version>`, and install from the new tag once to confirm it reports
   the new version.
8. **If the workflow failed, or the project has none.** It creates nothing when the
   changelog has no `## <version> - <date>` section, the section has no entry, or the
   version is not `X.Y.Z`. Re-run a run that only broke (a network error). Otherwise fix the cause in its own PR. A
   later push does not release, because only the commit that changed the version does,
   so then publish by hand on the release's merge commit:
   `devloop release-notes --version <version> > <notes>`, then
   `gh release create v<version> --target <merge sha> --notes-file <notes>`.

## When not to use

- Adding an entry for a change: add a fragment in that change's PR.
- Never tag a commit that is not on the default branch, and never move a published tag.

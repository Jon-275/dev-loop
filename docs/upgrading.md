# Upgrading

Upgrade in its own pull request:

1. Install the new version: `uv tool install --force git+https://github.com/Jon-275/dev-loop@v<version>`.
2. Set `framework.version` in `devloop.toml` to its minor version, such as `"0.7"`.
   `devloop doctor` fails while the two differ.
3. Point every shared workflow call at the new tag.
4. Run `devloop sync`. It rewrites the managed blocks of `AGENTS.md`, every rendered skill
   and the adapters, and leaves the project's own text and skills untouched.
5. Read the changelog's entries for each version you skip, especially "Breaking
   changes", and apply what they say.
6. Run `devloop doctor --check` and the project's own gates, then commit everything
   together.

## From 0.11 to 0.12

- **Model reviews.** `review.yml` now reviews only the owner, members, collaborators and
  trusted bots unless a maintainer adds the approval label. If you passed no `gate` and
  want every pull request reviewed, pass `gate: everyone`. An empty `gate` is an error.
  To let outside pull requests be reviewed, trigger on `labeled` and call `unapprove.yml`.
- **Hygiene checks.** `pr-hygiene.yml` reads `[docs]`, `[pr]` and `[changelog]` from the
  base branch. A pull request that changes one of them is judged by the old values, so
  merge the settings change first. A pull request that touches `devloop.toml` now runs
  the full code checks.
- **Statuses.** The gate job of `review.yml` needs `contents: read`. A pull request from
  a fork whose head commit is also the head of a branch in your repository gets no
  status ([why](security.md#a-status-belongs-to-a-commit)).
- **New, and optional.** The [merge gate](gate.md) can replace one required status per
  reviewer with one status. Nothing changes until you call `gate.yml`.

A new minor version may add skills to the default `skills.include`; a project that sets
`skills.include` itself keeps its own list. See the [changelog][changelog] for what
changed in each version.

[changelog]: https://github.com/Jon-275/dev-loop/blob/main/CHANGELOG.md

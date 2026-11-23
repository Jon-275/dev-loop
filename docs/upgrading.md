# Upgrading

Upgrade in its own pull request:

1. Install the new version: `uv tool install --force git+https://github.com/Jon-275/dev-loop@v<version>`.
2. Set `framework.version` in `devloop.toml` to its minor version, such as `"0.4"`.
   `devloop doctor` fails while the two differ.
3. Point every shared workflow call at the new tag.
4. Run `devloop sync`. It rewrites the managed blocks of `AGENTS.md`, every rendered skill
   and the adapters, and leaves the project's own text and skills untouched.
5. Read the changelog's entries for each version you skip, especially "Breaking
   changes", and apply what they say.
6. Run `devloop doctor --check` and the project's own gates, then commit everything
   together.

A new minor version may add skills to the default `skills.include`; a project that sets
`skills.include` itself keeps its own list. See the [changelog][changelog] for what
changed in each version.

[changelog]: https://github.com/Jon-275/dev-loop/blob/main/CHANGELOG.md

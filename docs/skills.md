# Skills

devloop ships these skills, and `devloop sync` renders every one named in
`skills.include` (all of them by default) for every tool. Each uses only portable
frontmatter, names no agent-specific tool, runs its commands through `devloop`, git and
`gh`, and says when not to use it; tests enforce all of that.

| Skill | Use it to |
|---|---|
| `devloop-setup` | set up or repair the project's devloop layout |
| `lessons-intake` | read the top review lessons and binding decisions before changing code |
| `pr-workflow` | take one change from a fresh branch to a merged pull request |
| `split-stack` | plan a change over the size limit as a stack of small PRs |
| `stateful-change` | write states, identities and invariants, then the end-state test, then the code |
| `review-locally` | run the model reviewers on the branch before pushing |
| `address-review` | work through one review round and push once |
| `decision-record` | record an owner's decision in `docs/decisions/` |
| `testing-and-ci` | write tests that can fail and CI that fails closed |
| `python-package` | scaffold a typed package and prove it from its wheel |
| `release` | bump the version, build the changelog, tag and publish |
| `council-design` | draft and prove the project's council from the role library |
| `council-review` | review a change as the council, every serious finding proven |

A project's own skills go in `devloop/skills/<name>/SKILL.md` and are rendered the same
way. Never install a community skill unreviewed: malicious skills have been found in
public registries.

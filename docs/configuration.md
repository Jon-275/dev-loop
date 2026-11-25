# Configuration

`devloop.toml` is the only place a project differs from the framework. Every key is
optional except `framework.version`; an unknown section or key is an error. Globs match
POSIX paths from the project root, and `*` also crosses `/`.

## [framework]

| Key | Default | Meaning |
|---|---|---|
| `version` | (required) | The devloop minor version the project is pinned to, such as `"0.7"`. `devloop doctor` fails when the installed devloop differs. |

## [pr]

| Key | Default | Meaning |
|---|---|---|
| `max_lines` | `800` | The most added plus deleted lines a pull request may change. |
| `exclude` | `[]` | Globs of files left out of the count, such as lock files. Skill copies are always left out. |
| `exclude_blocks` | `[]` | Generated blocks inside hand-written files, each `{ path, start, end }`: the lines from `start` to `end` are left out. |
| `large_label` | `"large-pr-approved"` | The label that lets a pull request over the limit pass. |

## [tiers]

| Key | Default | Meaning |
|---|---|---|
| `1` | `[]` | Globs of tier-1 (riskiest) files. |
| `3` | `[]` | Globs of tier-3 files, such as docs. Everything else is tier 2. |

## [review]

| Key | Default | Meaning |
|---|---|---|
| `providers` | `{}` | Reviewers per tier, such as `{ 1 = ["gemini", "reflection"], 2 = ["gemini"] }`. Empty: every reviewer reviews every tier. |
| `blocking_severities` | `["P1", "P2"]` | Severities posted as threads that block the merge; the rest go in the summary. |
| `lenses_dir` | `"devloop/lenses"` | Folder of extra review instructions, one Markdown file per reviewer. |
| `files` | code and config types | Globs of the files a model reviews. |
| `high_effort` | `["*"]` | Globs that make a pull request reviewed at high effort. |
| `feedback` | `""` | The feedback store `devloop feedback` writes and the prompt learns from. |
| `lessons` | `""` | The patterns file `devloop lessons` writes and every reviewer checks first. |
| `reviewers` | `{}` | Per-reviewer settings, one `[review.reviewers.NAME]` table each (below). |

### [review.reviewers.NAME]

| Key | Default | Meaning |
|---|---|---|
| `kind` | built in | The API: `gemini`, `openai` (any OpenAI-compatible endpoint) or `anthropic`. |
| `model` | built in | The model name. |
| `base_url` | built in | The API's base URL, read only from the trusted checkout. |
| `key` | `<NAME>_API_KEY` | The environment variable holding the API key. |
| `dimensions` | `{}` | Named review dimensions, such as `{ D1 = "Correctness" }`. |
| `summary` | `""` | The instruction for the reply's summary; empty for the default. |

## [labels]

| Key | Default | Meaning |
|---|---|---|
| `prefixes` | `["accepted", "rejected"]` | The words a labelled reply starts with, each followed by a colon. |
| `wait_for` | `[]` | Commit statuses that must finish before `Review labels` can pass. |
| `wait_for_checks` | `[]` | Check runs (review apps) that must finish first. |

## [lessons]

| Key | Default | Meaning |
|---|---|---|
| `seed` | `"devloop/lessons_seed.json"` | The bug classes and their keywords. |
| `doc` | `"docs/lessons-learned.md"` | The generated lessons page. |
| `agents` | `"AGENTS.md"` | The file whose `lessons:start` to `lessons:end` block gets the top rules. |
| `repo` | `""` | `owner/name`, to make PR numbers links. |

A class in the seed may carry `enforced_by`, naming the check that fails on it (a lint rule,
a test). Its rule is then left out of the `AGENTS.md` block, which names it in one line with
its check; the lessons page and the reviewers still get the rule.

## [agents]

| Key | Default | Meaning |
|---|---|---|
| `tools` | every supported tool | The coding agents in use: `claude`, `codex`, `gemini`, `antigravity`, `cursor`, `copilot`. |
| `skills_targets` | `.claude/skills`, `.agents/skills`, `.github/skills` | Where rendered skills go; together they must serve every tool. |

## [skills]

| Key | Default | Meaning |
|---|---|---|
| `include` | every shipped skill | The framework skills to render ([skills](skills.md)). |
| `project` | `"devloop/skills"` | The folder of the project's own skills. |

## [council]

| Key | Default | Meaning |
|---|---|---|
| `roles_dir` | `"devloop/council"` | The folder of role cards. |
| `max_reviewers` | `{ 1 = 5, 2 = 2 }` | The most council reviewers per tier; `devloop council check` fails a council over it. |
| `require_status` | `[1]` | The tiers whose pull requests must have a council review. `devloop council report` judges a report against it and `council.yml` posts the `Council review` status, which branch protection can require. |
| `declined` | `[]` | Library packs the owner chose not to add, so `devloop doctor` does not warn about their signals. |

## [changelog]

| Key | Default | Meaning |
|---|---|---|
| `dir` | `"changes"` | The folder of changelog fragments. |
| `file` | `"CHANGELOG.md"` | The changelog the fragments are built into. |
| `sections` | breaking, added, changed, fixed, security | Fragment section names and their headings. |
| `code` | `["src/*", ".github/*", "pyproject.toml"]` | Globs of code that should come with a fragment. |

## [docs]

| Key | Default | Meaning |
|---|---|---|
| `files` | Markdown and images in `docs/` | Globs of documentation files; a change to only these skips the code checks. |
| `tests` | `["tests/*test_*.py"]` | Test files that still run on a docs-only change when they read a `.md` file. |

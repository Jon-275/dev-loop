# Contributing to dev-loop

This page says how to get set up, what a change needs, and what happens to a pull
request after you open it.

## Get set up

You need [uv](https://docs.astral.sh/uv/) and Python 3.12. From a fresh clone:

```sh
uv sync
.venv/bin/python -m pytest
```

## What a change needs

- One concern per pull request, about 800 changed lines or fewer.
- A test that fails without your change. For a workflow, the tests in
  `tests/test_workflows.py` read the YAML itself.
- Type hints and a docstring on every source function. Type errors are fixed, not
  suppressed.
- A changelog entry as a file in `changes/`; [changes/README.md](changes/README.md) has
  the format.
- The checks pass:

  ```sh
  .venv/bin/ruff check . && .venv/bin/ruff format --check .
  .venv/bin/mypy
  .venv/bin/python -m pytest
  .venv/bin/devloop doctor --check
  .venv/bin/mkdocs build --strict
  ```

[AGENTS.md](AGENTS.md) gives a coding agent the same rules. `docs/decisions/` holds the
decisions the code must follow, so read the relevant one before you change that area.

## What happens to your pull request

1. The maintainer reads it first. Workflows do not run on a pull request from outside
   until the maintainer approves the run.
2. Two outside model reviewers, Gemini and Reflection, receive the changed files and
   their diff only after the maintainer has looked and added the label
   `ai-review-approved`. A later push removes the label, so new commits are looked at
   before they are sent. The [security model](docs/security.md) says what each vendor
   receives. If you do not want your change sent to them, say so in the pull request
   before it is reviewed.
3. Each review comment gets an answer that starts with `accepted:` or `rejected:`, and
   the pull request is merged when the checks pass and every comment is settled.

There is one maintainer, so an answer can take several days.

## Licence

dev-loop is under the [Apache License 2.0](LICENSE). Section 5 of that licence covers
contributions you submit.

## Security

Report a vulnerability in private, as [SECURITY.md](SECURITY.md) describes, not in an
issue or a pull request.

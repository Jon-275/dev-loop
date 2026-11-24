"""The conformance benchmark: whether a coding agent follows devloop's rules.

A small fixture project (``project/``) is copied to a scratch directory, set up with
devloop's layout for one tool, and given a task (``tasks``). The result is judged by
checks (``checks``) built on devloop's own gates: tests that fail without the fix, a
changelog fragment, the size limit, a feature branch, no ``type: ignore``, labelled
review replies. Runs are local only: an agent run needs the user's own login, which
never goes to CI (docs/decisions/0006-local-benchmark.md).
"""

# 0006: The conformance benchmark runs locally

Status: accepted, 2026-11-24.

## Decision

The conformance benchmark, which runs a real coding agent on the fixture project's tasks,
runs only on a maintainer's own machine, with their own agent login. No workflow in
this repository runs it, and no agent API key is stored as a secret here.

## Why

This repository is public, and so are its Actions logs and artifacts. A stored secret is
masked in logs, but an agent run could print it in a changed form, and a transcript or
artifact is readable by anyone. Keeping agent runs local removes that risk. The cost is
that the benchmark runs when a maintainer runs it, not on a schedule.

## How to apply

- The benchmark's checks and its fixture are tested in CI with scripted solutions, so no
  agent and no key is needed there.
- Once the runner lands (the next step), run the benchmark before a release and after a
  supported agent CLI changes, and note its scorecard in the release pull request; the
  `release` skill gains that step with the runner.

## Scope

The conformance benchmark only. Model reviews keep their keys, used only from
`pull_request_target` on the default branch's code (0002, 0003).

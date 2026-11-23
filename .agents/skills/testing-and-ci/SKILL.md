---
name: testing-and-ci
description: Write tests that prove behaviour (each fails when its fix is reverted), check the installed package rather than the checkout, and build or repair CI workflows with fast and slow lanes that fail closed. Use when adding tests, chasing a CI failure or changing a workflow; not for choosing what to build or for publishing a release (release).
---

# Testing and CI

A test is evidence only if it can fail. CI is evidence only if a missing prerequisite or a
failed command fails the run.

## Tests

1. **Reproduce first.** For a bug, write the test that fails on the current code, then fix
   it. Revert the fix once and watch the test fail.
2. **Test observable behaviour**: inputs and outputs, end states, error messages, files
   written. Do not assert on generated source text or private structure.
3. **Properties for state.** For code with state or retries, add a property test over
   random sequences of operations and faults that checks the end state (see
   `stateful-change`).
4. **Concurrency** uses events or barriers, never sleeps, so the test is deterministic.
5. **Errors are tested too:** an unreadable input is an error, never "empty"; a failed read
   never records a lasting verdict.
6. **Never weaken a test to pass:** no skip, no wider tolerance, no deleted assertion,
   unless the behaviour really changed and the PR says so.

## The installed package

Build the wheel and install it in a fresh environment outside the checkout, then run the
CLI and imports from there. An editable install does not prove the wheel ships its data
files, templates or entry points.

## CI workflows

1. **Lanes:** a fast lane on every push (lint, types, unit tests) and a slower lane for
   integration, installed-package and benchmark checks. A docs-only change may skip code
   lanes only through a check that fails closed (`devloop changes`).
2. **Fail closed:** a missing tool, secret or input fails the job; a skipped check is
   reported as skipped, never as passed.
3. **Untrusted code:** pull request code runs only in jobs without secrets and with a
   read-only token. A trusted job (`pull_request_target`) never checks out or runs the
   pull request's code.
4. **Pin actions** to a full commit SHA with the version in a comment, and do not persist
   checkout credentials.
5. **Evidence:** a hosted result is a run URL. A local run is not hosted CI; say which one
   you have.

## When not to use

- Releasing: use `release`.
- Never mark a flaky test as skipped to get green; find the race or the shared state.

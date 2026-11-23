# 0002: Model reviews run from the base branch and use the standard library

Status: accepted, 2026-11-22.

## Decision

`devloop review` is ported from gtm_toolkit's review core with its behaviour kept: the
prompt, the reply rules, thread markers, de-duplication, split and incremental reviews.
Three things change:

- Gemini and OpenAI-compatible APIs are called with the standard library, not their
  SDKs, so devloop keeps no runtime dependencies.
- Which reviewers run, what they read and what blocks a merge come from `devloop.toml`.
  Findings below `review.blocking_severities` go in the summary only.
- `review.yml` installs devloop and reads its settings from the base branch's checkout.
  The pull request's checkout is only read through Git.

## Why

A review job holds an API key and a token that can post. Everything that runs, and
everything that steers where a key goes (the endpoint, the lens, the feedback), must
come from code the maintainers already merged. Thread markers and status names match
gtm_toolkit's, so its open threads keep counting when it switches to devloop.

## Consequences

- An SDK's own retry and error types are gone. Retries are devloop's alone, and errors
  are classified by HTTP status and error code.
- An Anthropic reviewer, which should use the official SDK, comes as an optional extra
  in a later change.

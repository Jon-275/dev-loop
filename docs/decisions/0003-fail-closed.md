# 0003: Every check fails closed

Status: accepted, 2026-11-23.

## Decision

A check that cannot judge never passes. A missing key, input or tool, an error from a
model or from GitHub, a reply in the wrong shape, or a read that failed makes the check
fail (or stay pending while it waits), and says why. Advisory checks, such as the size
check on an unexpected event, pass only with a note naming what they could not read.

## Why

A gate that passes when it breaks is worse than no gate: it reports "healthy" for a change
nobody judged. "Not judged" and "healthy" must never look the same.

## How to apply

- Catch only the exact error you mean, and turn it into a failure with a reason.
- Never record a lasting verdict, or write a store, from a read that failed.
- A test for every check covers its failure path, not only its pass.

## Scope

Every devloop command and shared workflow.

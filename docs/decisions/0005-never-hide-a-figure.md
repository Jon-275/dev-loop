# 0005: Never hide a finding or a figure

Status: accepted, 2026-11-23.

## Decision

Anything a devloop command leaves out is counted and named in its output: excluded files
in the size check, findings below the blocking severities in the review summary,
suppressed findings, pull requests a metrics query truncated, runs whose tokens were not
measured. Changing how a reported figure is computed needs the owner's approval.

## Why

A figure with silent exclusions cannot be trusted or reproduced, and a hidden finding
looks the same as no finding. Naming what was left out keeps every number checkable.

## How to apply

- Every filter in a report prints how many items it removed, and which.
- A change to a reported figure says so in its pull request and changelog entry.

## Scope

Every devloop report, status description and summary.

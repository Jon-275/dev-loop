# 0004: Every review finding gets a labelled reply

Status: accepted, 2026-11-23.

## Decision

Every finding thread on a pull request needs a reply from a member that starts with a
label word from `labels.prefixes` (`accepted:` or `rejected:` by default). The
`Review labels` status fails until each one has it, resolved or not, and stays pending
until the reviews in `labels.wait_for` have finished.

## Why

The labels are the loop's only ground truth. They feed the reviewers' examples, the
suppression of findings rejected three times, the lessons, and the precision figures in
`devloop metrics`. An unlabelled thread is lost to all of them.

## How to apply

- Reply in one line: the label, then what changed or why the finding is wrong.
- Only a member's reply counts, so an outside comment cannot steer the feedback.
- A P1 finding is never suppressed by rejections.

## Scope

Every project that requires the `Review labels` status.

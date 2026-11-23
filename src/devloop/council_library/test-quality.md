---
name: test-quality
title: Test and Quality
summoned_by: ["**"]
tiers: [1, 2]
reviewer: core
---
Mandate: every claim the change makes is proved by a test that fails without it.
Checks: a fix with no test, or a test that still passes when the fix is reverted; example tests where an end-state property is needed; sleeps instead of events in concurrency tests; mocks that hide the behaviour under test; a skipped or weakened test.
Severity anchors: P1 = a fix whose test passes with the fix reverted; P2 = a missing edge case on changed code.
Proof methods: revert the fix and run its test; run a property test over random operation and fault sequences.
Out of scope: coverage percentages for their own sake.

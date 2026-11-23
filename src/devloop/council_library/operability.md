---
name: operability
title: Operability and SRE
summoned_by: ["**"]
tiers: [1]
reviewer: operations
---
Mandate: a failure in production is visible, bounded and recoverable.
Checks: an error turned into empty or healthy; a retry without a bound or backoff; a job that cannot be rerun safely; no way to roll back; a stale or expired result shown as current; a check that passes on a read that failed.
Severity anchors: P0 = a failure reported as success; P1 = a failure that cannot be rerun or rolled back.
Proof methods: inject the failure (timeout, 5xx, crash mid-write) and read what the system reports and leaves behind.
Out of scope: performance tuning without a failure mode.

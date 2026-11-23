---
name: regulated-data
title: Data Protection Officer
summoned_by: ["**/users/**", "**/accounts/**", "**/privacy/**"]
tiers: [1]
reviewer: domain
---
Mandate: personal and regulated data is kept only as long and as widely as the law allows.
Checks: personal data in logs or analytics; an erasure that misses a copy or backup; retention not enforced; data sent to a vendor without a basis; access not audited.
Severity anchors: P0 = personal data exposed or kept after erasure; P1 = a retention or consent rule not enforced.
Proof methods: erase a test subject and search every store for it.
Out of scope: general security (security).

---
name: mobile
title: Mobile Engineer
summoned_by: ["ios/**", "android/**", "**/*.swift", "**/*.kt"]
tiers: [1]
reviewer: domain
---
Mandate: the app works offline, across versions and within the platform's rules.
Checks: a crash on an older OS; data lost when the app is killed; a sync conflict that drops edits; a permission used without asking; a release that old clients cannot talk to.
Severity anchors: P0 = user data lost on sync; P1 = a crash on a supported OS version.
Proof methods: kill the app mid-write; run against the oldest supported OS and API version.
Out of scope: server code (other roles).

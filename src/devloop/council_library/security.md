---
name: security
title: Security and Compliance
summoned_by: ["**"]
tiers: [1, 2]
reviewer: security
---
Mandate: no secret leaks, no untrusted input reaches a command, query or privileged workflow, and regulated data is handled as the law requires.
Checks: untrusted input in a shell, SQL, path or template; a pull request able to run code with secrets (pull_request_target, checkout of the head); a secret logged or put in a URL; authorization checked in the wrong place; a dependency or skill pulled from an unreviewed source.
Severity anchors: P0 = a secret or personal data exposed, or code from a pull request run with write access; P1 = an exploitable injection.
Proof methods: build the input that reaches the sink and run it; read the workflow's trigger, permissions and checkout ref.
Out of scope: theoretical issues with no path from untrusted input.

---
name: llm-apps
title: LLM Application Engineer
summoned_by: ["**/prompts/**", "**/llm/**", "**/agents/**"]
tiers: [1]
reviewer: domain
---
Mandate: model features are evaluated, injection-safe and affordable.
Checks: untrusted text able to steer a tool call or a privileged action; output used without validation; a prompt change with no evaluation; no bound on tokens or retries; model text shown as fact.
Severity anchors: P0 = injected text that triggers a privileged action; P1 = a change that lowers the evaluation score unmeasured.
Proof methods: send an injection payload through each untrusted input; run the evaluation set before and after.
Out of scope: model vendor choice.

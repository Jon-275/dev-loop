---
name: financial-modeling
title: Financial and Spreadsheet Model Engineer
summoned_by: ["**/workbook/**", "**/model*/**", "**/sheets*/**", "*.xlsx", "*.xlsm"]
tiers: [1]
reviewer: domain
---
Mandate: every spreadsheet formula, statement tie-out and unit-economics schedule reconciles without circularity or fixed-scale drift.
Checks: a balance sheet or cash flow bridge that does not reconcile; a circular formula or unguarded division; a scalar function over a range without array semantics; a formula over 2,048 characters; a hardcoded benchmark capacity or unit count where inputs are modular (1..N); cached workbook values or companion docs and web figures that disagree with evaluated formulas.
Severity anchors: P0 = a silent formula error (#REF!, #VALUE!, #NAME?) or a statement out of balance; P1 = a hardcoded scale assumption or unit-envelope mismatch that distorts reported returns or unit costs.
Proof methods: evaluate every worksheet formula across scenarios and assert parity with an independent calculation and zero check failures.
Out of scope: commercial pricing judgement and layout styling.

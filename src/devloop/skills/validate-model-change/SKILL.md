---
name: validate-model-change
description: Validate a change to model inputs, formulas, policy, execution, or reported results against independent evidence. Use for financial, operational, or predictive models when correctness, freshness, or a release claim can change.
---

# Validate a model change

Use the project's existing reference, fixture builder, comparison commands, and release gates. Record new tolerances or limits with the reason before comparing outputs.

1. Identify the changed outputs and the decision they support. Trace the material inputs, units, policy and model versions, and date or horizon through calculation, saved state, and report. Name the invariants and the valid rounding or comparison tolerances.
2. Use fictional, versioned fixtures and an independent source of expected results. Keep one ordinary case, one boundary, and the smallest case that exposes the suspected defect. Pick cases for the changed behavior. Reuse evidence for unaffected behavior when the compared inputs and scope still apply.
3. For a stateful model, change an input while retaining a prior result. Verify the result goes stale, the next valid run publishes a fresh result, and an interrupted run leaves the last complete batch intact where the project promises recovery. Include the user-visible saved or exported result in the comparison.
4. For a performance change, compare the same workload before and after on the same runtime and settings. Count cold and warm runs separately, retain individual timings, and reconcile each timed output. Keep the promotion threshold and measured result in the record.
5. Record the tested revision, fixture and artifact identities, environment, comparisons, and limitations. Distinguish native behavior, exported output, and platform coverage. Keep private inputs and raw captures out of public evidence.

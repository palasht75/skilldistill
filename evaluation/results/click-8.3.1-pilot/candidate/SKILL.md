---
name: repair-cli-token-normalization
description: Use this skill when a localized behavior regression needs a minimal production-code
  fix, with nearby source/test inspection and focused then adjacent test validation.
---

# Workflow
1. Search for the failing behavior and related terminology with `rg` to locate the most likely implementation and test sites.
2. Open the matched implementation file and nearby parser/type/command code with `sed` to understand the narrow behavior path before editing.
3. Check whether there is an existing focused regression test file for the behavior and use it as the primary reference point.
4. Make the smallest production-code change that addresses the behavior.
5. Validate first with one focused regression test, then with a slightly broader nearby-test slice that covers adjacent behavior.
6. Prefer a production-only fix unless the local evidence clearly requires a test update.

# Branching
- If the regression involves normalization or canonicalization, inspect the normalization helper and the adjacent parser or command-resolution path.
- If the regression involves choice or type handling, inspect the value-conversion path and any helper that normalizes candidate values.
- If the regression involves command lookup or fallback matching, inspect the command-resolution path that compares names or aliases.
- If a dedicated regression test already exists, use that before widening validation.
- If the behavior spans more than one nearby module, keep the code change narrow and validate both the direct case and the adjacent slice.

# Verification
- Run one focused test case first, targeting the exact regression.
- Run a nearby slice afterward that includes a related test module or neighboring behavior.
- Treat both runs as required evidence before calling the fix stable.
- Only claim coverage for the behavior the tests actually exercise.
- In the observed trajectories, the useful pattern was one focused `pytest` case followed by a small nearby `pytest` slice, both passing.

# Pitfalls
- Do not infer the exact patch mechanics from a prose summary alone.
- Do not generalize one normalization regression to all normalization behavior in the repository.
- Do not widen the change to tests, config, docs, or generated files unless the local evidence requires it.
- Do not treat unverified causal explanations as facts if tool output did not show them.

# Review notes
- The trajectories support a family of similar small regressions, not one proven universal root cause.
- The exact implementation layer varied across trajectories, so inspect the local source before choosing the branch to patch.
- The strongest repeated pattern is minimal production-only change plus focused and nearby test validation.

# Click 8.3.1 evaluation result

- Study: `click-8.3.1-token-normalization-pilot`
- Provider smoke: **blocked**
- Source collection: **passed**
- Candidate generation: **passed** via `codex-chatgpt-fallback`
- Candidate SHA-256: `2ff87394f9ec91a4d15f56d1b4c21a4601eec33d5768bd6df1f7cd1af5fb412e`
- One-time candidate generation: 4 calls, 50855 input tokens, 5259 output tokens

## Held-out results

| Task | Condition | Verified | Runs | Input | Cached | Fresh input | Output | Latency ms | Turns | Tool calls |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `nested-context-normalization` | baseline | 3 | 3 | 399023 | 342528 | 56495 | 5757 | 137826 | 3 | 32 |
| `nested-context-normalization` | candidate | 3 | 3 | 461533 | 408192 | 53341 | 7108 | 168431 | 3 | 33 |
| `shell-token-splitting` | baseline | 3 | 3 | 381430 | 317696 | 63734 | 4271 | 122541 | 3 | 26 |
| `shell-token-splitting` | candidate | 3 | 3 | 388826 | 330496 | 58330 | 4420 | 136022 | 3 | 24 |

## Decision

- Success non-inferior: **True**
- Negative control without regression: **True**
- Net new held-out success: **False**
- Reported-token reduction of at least 10%: **False**
- Evidence of improvement on this task set: **False**
- Efficiency claim supported: **False**

This controlled pilot does not support a universal performance or savings claim.

## Post-holdout protocol finding

- **The frozen candidate describes any localized behavior regression, rather than limiting its trigger to normalization behavior.** The negative-control pass counts remain valid execution outcomes, but they do not establish that automatic trigger selection would correctly avoid the unrelated task.
- **The initial holdout records omitted per-run candidate, schedule, and study hash bindings.** The hashes were backfilled from the candidate and schedule that were already frozen before execution; patches, verifier outcomes, usage, and timing were not changed.

The frozen candidate was not changed or regenerated. Addressing this finding requires a new task split.

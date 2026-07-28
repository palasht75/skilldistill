# Behavioral evaluation

This document defines the first reproducible real-repository evaluation for
`skilldistill`. It separates three questions that unit tests cannot answer:

1. Can the configured provider produce a structurally valid skill candidate?
2. Can several successful repository trajectories produce a candidate that is
   useful on an unseen but related task?
3. Does that candidate avoid harming an unrelated negative-control task?

## Current status

The first study completed on 2026-07-28. All predeclared repository runs
finished without an infrastructure timeout. The direct OpenAI provider smoke
was blocked by account quota, so candidate generation used the predeclared
authenticated Codex fallback with the same `gpt-5.4-mini` model.

| Stage | Status | Evidence |
|---|---|---|
| Offline preflight | Passed | 134 project tests; Ruff, dunnit, build, package scan; five seeded failures confirmed |
| Provider smoke test | Blocked | `gpt-5.4-mini`; HTTP 429 `insufficient_quota` |
| Three source trajectories | Passed | All three passed on their first attempt |
| Candidate generation and freeze | Passed | Four fallback calls; candidate SHA-256 recorded |
| Twelve held-out runs | Passed | All baseline and candidate runs completed and passed |
| Aggregate conclusion | No improvement shown | No new success; trigger-scope limitation recorded after holdouts |

The sanitized [result report](../evaluation/results/click-8.3.1-pilot/REPORT.md),
[metrics](../evaluation/results/click-8.3.1-pilot/metrics.json), candidate,
source traces, schedule, and individual run manifests are published under
`evaluation/results/click-8.3.1-pilot/`. Raw streams and temporary repositories
remain ignored.

## Result

The candidate and baseline both passed all three repetitions of the positive
holdout and all three repetitions of the negative control:

| Task | Condition | Verified | Input | Cached | Fresh input | Output | Latency ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| Nested-context normalization | Baseline | 3/3 | 399,023 | 342,528 | 56,495 | 5,757 | 137,826 |
| Nested-context normalization | Candidate | 3/3 | 461,533 | 408,192 | 53,341 | 7,108 | 168,431 |
| Shell-token negative control | Baseline | 3/3 | 381,430 | 317,696 | 63,734 | 4,271 | 122,541 |
| Shell-token negative control | Candidate | 3/3 | 388,826 | 330,496 | 58,330 | 4,420 | 136,022 |

Input already includes the cached subset; fresh input is `input - cached`, so
the categories are not added together. Reasoning-token counts were unavailable
and remain `null`.

Candidate generation used four `gpt-5.4-mini` calls: 50,855 input tokens
(20,480 cached; 30,375 fresh), 5,259 output tokens, and 85,183 ms. Its frozen
SHA-256 is
`2ff87394f9ec91a4d15f56d1b4c21a4601eec33d5768bd6df1f7cd1af5fb412e`.

The candidate was behaviorally non-inferior and did not regress the negative
control. It did not create a new success: baseline already solved every run.
On the positive holdout, reported input plus output tokens and latency were
higher with the candidate. This study therefore provides no evidence of
behavioral improvement or token efficiency, and supports no savings claim.

One protocol limitation was found only after the holdouts: the frozen
candidate's description applies to localized behavior regressions generally,
not specifically to normalization behavior. The negative-control executions
still passed, but they cannot establish that automatic trigger selection would
avoid that unrelated task. The candidate was not changed or regenerated. The
machine-readable
[post-holdout finding](../evaluation/results/click-8.3.1-pilot/post-holdout-findings.json)
requires a new candidate and task split for the next study.

Final integrity review also found that the initial per-run records omitted
candidate, schedule, and study hash fields. Those fields were backfilled from
the candidate and schedule already frozen before execution; patches, verifier
outcomes, usage, and timing were not changed. The harness now writes and
validates all three bindings at run time.

## Frozen study configuration

The first study uses the following configuration:

| Component | Pinned value |
|---|---|
| Repository | `pallets/click` |
| Click revision | `1d038f270701498433cb432f54db89f95f07a845` (8.3.1) |
| Python | 3.12 |
| pytest | 8.4.2 |
| Distillation provider model | `gpt-5.4-mini` |
| Agent reasoning effort | medium |
| Source attempts | no more than two per task |
| Holdout repetitions | three per task and condition |

The requested agent model and observed Codex CLI version are recorded. This
Codex JSONL version did not return an independently observed model identifier,
so `returned_model` is `null`; the configured override remains recorded as
`requested_model`. If `gpt-5.4-mini` is unavailable as the agent model, one
available agent model must be chosen before source collection and used
unchanged for every repository run. The provider smoke test and candidate
generation remain pinned to `gpt-5.4-mini`.

Every task starts from a history-free archive of the pinned Click revision in a
fresh temporary Git repository. It receives the same Python and pytest
versions, sandbox policy, prompt shape, time budget, and verifier policy.
User-level agent instructions are disabled. Repository paths and secrets are
removed from committed artifacts.

## Predeclared tasks

Three related source tasks independently disable:

- option-name normalization;
- choice-value normalization; and
- command-name normalization.

Each source attempt must satisfy its focused regression test plus the shared
normalization and parser regression suite. At most two attempts are permitted
for each source task. The first verifier-backed success is selected; every
failed attempt is still recorded.

The candidate is then generated from the three selected normalized
trajectories with exactly four provider calls: one trajectory-local extraction
per source and one consolidation call. Its contents and SHA-256 digest are
frozen before either holdout task is run.

The positive holdout disables inheritance of `token_normalize_func` into
nested contexts. The negative control changes `split_arg_string` shell
tokenization, for which the normalization candidate should not prescribe a
solution.

## Held-out comparison

The experiment has two conditions:

- **Baseline:** the agent receives the task and repository context only.
- **Candidate:** the agent receives the same inputs plus the frozen candidate.

Each condition runs three times on each holdout task, for 12 runs total. Run
order is generated from a recorded seed before execution. Runs use fresh
repositories and identical settings; no state, transcript, or patch is reused.
The candidate is injected only in the candidate condition.

Explicit injection tests the value of the frozen content once supplied; it
does not test automatic skill discovery or trigger selection. The
post-holdout trigger-scope finding further limits what can be inferred from
the negative-control result.

The holdout prompts, verifiers, seed, timeouts, tool versions, agent model, and
candidate digest are recorded in a machine-readable manifest before the first
holdout starts. Neither prompts nor the candidate may be edited after any
holdout result is observed. A revision requires a new candidate, seed, and task
split.

Once a candidate is frozen or a holdout result exists, mutating stages refuse
to reuse that artifact root. An interrupted holdout stage may resume only when
every existing result matches its run identity plus the frozen candidate,
schedule, and study hashes. Aggregation repeats the same validation.

## Metrics and decision rule

Every run records:

- focused-verifier and shared-regression outcomes;
- changed files and patch digest;
- wall-clock latency, turns, retries, and tool calls;
- fresh input, cached input, output, and reasoning tokens when exposed by the
  runtime; and
- failures, missing measurements, and termination reason.

Token categories remain separate and unavailable values remain `null`; the
report never infers a total from incomplete runtime data. Candidate-generation
latency and tokens are reported separately from held-out execution.

The candidate shows evidence of improvement only when:

1. candidate verified-success count is not lower than baseline;
2. the negative control has no candidate-only regression; and
3. any reported token reduction is calculated only over verified successes
   with comparable token fields.

A single study supports only a result for this pinned task set. It does not
establish performance for other repositories, models, tasks, or agent
runtimes.

## Static candidate gate

The executed gate checked parseable frontmatter, portable naming, the literal
`Use this skill when` description prefix, credential and absolute-path
patterns, held-out canaries, and listed source-specific paths and literals. It
did not semantically verify that the description scoped the trigger to
normalization. That gap admitted the generic frozen candidate and was detected
only after the holdouts.

The harness is now strengthened for future task splits: the description must
contain a study-specific trigger term, declared shell snippets are limited to
the local inspection and test commands supported by source evidence, and
additional source-mutation literals are rejected. These checks are covered by
focused tests. They are not retroactively attributed to this frozen candidate.

A future gate failure stops the study before held-out execution. A corrected
attempt uses a fresh artifact root before any holdout is run, preserving the
failed generation record.

## Reproduction record

The committed study manifest is `evaluation/click-8.3.1.json`. Run its
non-paid checks and materialize the predeclared schedule before allowing
provider or agent execution:

```bash
python3.12 -m evaluation.harness preflight
python3.12 -m evaluation.harness plan \
  --artifacts evaluation/artifacts/pilot
```

After reviewing that schedule, the single paid command runs the provider smoke
test, source collection, candidate generation, candidate freeze, randomized
holdouts, and metric aggregation:

```bash
python3.12 -m evaluation.harness run \
  --artifacts evaluation/artifacts/pilot \
  --env-file .env \
  --execute
```

The artifact root contains `manifest.json`, `schedule.json`, `smoke/`, the
source attempts under `sources/<task>/<attempt>/`, the frozen
`candidate/SKILL.md` and `candidate/freeze.json`, individual
`holdouts/<run-id>/` records, and `metrics.json`. The publisher adds
`provenance.json` to the sanitized result.

`evaluation/artifacts/` is generated and uncommitted. The completed study was
curated through the allowlisted publisher:

```bash
python3.12 -m evaluation.harness publish \
  --artifacts evaluation/artifacts/pilot \
  --destination evaluation/results/click-8.3.1-pilot
```

The publisher retains normalized source traces, summary-only holdout records,
the frozen candidate, manifests, and aggregate metrics. Before replacing a
contained result directory, it recomputes the aggregate and validates every
run binding. It removes holdout event bodies and re-runs credential and
machine-path redaction. Raw agent streams, temporary repositories, provider
responses that may contain sensitive context, and credentials remain
uncommitted.

## Known limitations

This first study uses Codex to create and consume the evaluation trajectories.
Cursor parsing is covered by format fixtures, but there is no authenticated
Cursor end-to-end result yet. That remains a distinct validation gap until
consented Cursor exports or an authenticated Cursor runner are available.

The candidate trigger was too broad for the negative control to test
non-application or automatic selection. The operational run results remain
reported, but this validity gap requires a new task split.

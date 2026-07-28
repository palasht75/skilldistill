# skilldistill

**Turn selected agent trajectories into reusable, reviewable skill candidates.**

[![PyPI](https://img.shields.io/pypi/v/skilldistill)](https://pypi.org/project/skilldistill/)
[![CI](https://github.com/palasht75/skilldistill/actions/workflows/ci.yml/badge.svg)](https://github.com/palasht75/skilldistill/actions)
[![Python](https://img.shields.io/pypi/pyversions/skilldistill)](https://pypi.org/project/skilldistill/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Agents repeatedly discover useful procedures, but most of that knowledge stays
trapped in transcripts. `skilldistill` is a local-first Python library and CLI
that finds reusable evidence in those trajectories and turns it into portable
`SKILL.md` candidates.

It can:

- rank transcripts by transparent success evidence;
- distill one selected trajectory into a skill draft;
- synthesize one skill from several related trajectories;
- propose a revision informed by a frozen existing skill;
- surface lexical overlap candidates across an existing skill library;
- propose a non-destructive consolidation of overlapping skills; and
- ingest Claude Code and Cursor transcripts while keeping the core
  agent-agnostic.

Output defaults to a neutral review directory. `skilldistill` provides no
install or promotion command and never merges or prunes candidates
automatically.

## Research roots

`skilldistill` is inspired by
[Trace2Skill](https://github.com/Qwen-Applications/Trace2Skill), which extracts
trajectory-local lessons and consolidates them into skill updates, and
[MUSE-Autoskill](https://arxiv.org/html/2605.27366v1), which treats creation,
memory, management, evaluation, and refinement as one lifecycle, with reuse,
merging, and pruning handled by its management layer.

This package is not a reproduction of either research system. It provides a
lightweight open-source slice of that workflow:

```text
agent traces ─▶ normalize ─▶ score evidence ─▶ trajectory-local lessons
                                                       │
existing skill (optional) ─────────────────────────────┤
                                                       ▼
                                      consolidate ─▶ SKILL.md candidate

existing skills ─▶ lexical overlap candidates ─▶ human selection
                                                    │
                                                    ▼
                                           consolidation candidate
```

## Installation

The workflow documented below is available in version `0.2.0`:

```bash
python -m pip install "skilldistill==0.2.0"
```

To work from a source checkout:

```bash
git clone https://github.com/palasht75/skilldistill.git
cd skilldistill
python -m pip install -e .
```

The core depends only on PyYAML and works without a model provider. Optional
adapters are installed explicitly:

```bash
python -m pip install -e ".[openai]"
# or
python -m pip install -e ".[anthropic]"
```

## Five-minute quickstart

### 1. Find sessions worth distilling

```bash
skilldistill scan ~/.claude/projects
```

Example:

```text
[0.95] ~/.claude/projects/my-api/7f3a.jsonl
       goal: Fix the flaky retry logic in the payment client
       why:  substantial workflow (14 tool calls); low tool error ratio (7%);
             recorded success signal in tool result (tests/build/PASS);
             clean final summary;
             assistant-reported success (not independent verification)
```

Every score includes reasons. Tool-result evidence is weighted more strongly
than an assistant saying its own work succeeded.

### 2. Draft a skill

The CLI is offline by default, even if an API key exists in the environment:

```bash
skilldistill distill ~/.claude/projects/my-api/7f3a.jsonl
```

This writes a clearly labeled scaffold under `skill-drafts/`. For a generalized
model-written candidate, opt into a provider:

```bash
export OPENAI_API_KEY="..."
skilldistill distill ~/.claude/projects/my-api/7f3a.jsonl \
  --provider openai
```

### 3. Synthesize several related trajectories

One selected run can encode accidental, project-specific choices. Related
traces provide stronger evidence for invariant steps and legitimate variants:

```bash
skilldistill distill \
  traces/retry-api.jsonl \
  traces/retry-worker.jsonl \
  traces/retry-cli.jsonl \
  --provider openai \
  --name repair-retry-workflows
```

In model-backed mode, `skilldistill` performs one bounded lesson-extraction
call per trajectory and one conflict-aware consolidation call. The
consolidator is instructed to deduplicate repeated lessons, express legitimate
variations as conditional branches, and retain unresolved contradictions in
review notes.

Offline multi-session mode is deterministic. It emits the common tool-order
backbone, trace-specific variants, recorded outcomes, and observed errors as a
review scaffold without pretending to have performed semantic generalization.

### 4. Propose a revision from a frozen base skill

```bash
skilldistill distill traces/retry-regression.jsonl traces/retry-success.jsonl \
  --base-skill skills/retry-workflows/SKILL.md \
  --provider openai \
  --name retry-workflows \
  --output-dir skill-drafts
```

The base file is frozen input. The model proposes a complete replacement
candidate; it does not produce or apply a guarded patch. The candidate is
written separately so a reviewer can diff it against the base and reject any
lost behavior. Preservation-safe deepening is a research milestone, not a
current guarantee.

### 5. Find and review existing-skill overlap

Run the bounded, read-only lexical heuristic across a skill library:

```bash
skilldistill overlaps skills --threshold 0.35
```

It ranks pairs and reports shared terms. A high score is a review lead, not
evidence that the triggers or behavior are equivalent. After inspecting a
pair, explicitly ask for a consolidation candidate:

```bash
skilldistill consolidate \
  skills/review-python/SKILL.md \
  skills/review-security/SKILL.md \
  --provider openai \
  --name review-python-safely
```

The default output is `skill-drafts/`. Source skills are never edited or
deleted.

The offline consolidator identifies bounded exact repeated-line candidates and
preserves bounded, redacted descriptions, additional frontmatter, and source
bodies as inert review text. It is a deterministic scaffold, not a semantic
merge. The model-backed consolidator can reason about semantic overlap, but it
is still only a proposal: similar wording is not proof that two trigger scopes
should merge.

## Supported transcript sources

| Adapter | Input | Status |
|---|---|---|
| Claude Code | tolerant session JSONL | supported |
| Cursor Agent CLI | `stream-json` JSONL | supported |
| Cursor IDE | exported Agent Markdown | supported |
| Python API | normalized `Session` and `Event` objects | supported |
| Additional agents | transcript adapters | planned |

`--source` is a discovery hint: Cursor mode additionally recognizes exported
Markdown, while compatible JSONL is considered in every mode and normalized by
record shape.

Draft output always defaults to the neutral `skill-drafts/` review directory.
Use `--output-dir` only for another non-active review location. The CLI has no
install or promotion target.

### Review-first Cursor workflow

Capture a Cursor Agent CLI run, then distill it with the same neutral core:

```bash
cursor-agent -p "fix the retry bug and run the focused tests" \
  --output-format stream-json | tee cursor-run.jsonl

skilldistill distill cursor-run.jsonl \
  --provider openai \
  --name repair-retry-workflows
```

Cursor's current [`stream-json`
format](https://docs.cursor.com/en/cli/reference/output-format) includes user
messages, assistant deltas, tool calls/results, and a terminal result. For
older or partial streams that omit prompts, repeat `--goal` once per input in
the same order:

```bash
skilldistill distill run-api.jsonl run-worker.jsonl \
  --goal "Repair retry handling in the API client" \
  --goal "Repair retry handling in the worker"
```

If the project already has a Cursor skill library, add
`--compare-dir .cursor/skills` to warn about similar existing candidates.

Cursor IDE Agent conversations exported as Markdown can be scanned with:

```bash
skilldistill scan ./cursor-exports --source cursor --min-score 0.2
```

Exported Markdown usually contains less structured tool evidence than CLI
JSONL, so it commonly needs a lower selection threshold. Cursor remains an
adapter, not a separate distillation pipeline.

After reviewing the generated file, comparing it with overlapping skills, and
evaluating it on representative tasks, a human can copy the approved skill
folder into `.cursor/skills/`. The normal workflow intentionally keeps that as
a separate human action because [Cursor skills are discoverable by the
agent](https://cursor.com/blog/agent-best-practices).

## How distillation works

1. **Normalize.** Parsers turn unstable transcript formats into a small
   `Session`/`Event` model and skip unknown or malformed records.
2. **Select evidence.** Heuristics rank substantial sessions, recorded test or
   build success, low tool-error ratios, and clean outcomes.
3. **Extract local lessons.** Each trajectory is bounded, redacted, includes
   bounded tool calls and results, and is treated as untrusted input.
4. **Consolidate.** The multi-trace prompt asks the model to preserve repeated
   evidence, conditional variants, observed recoveries, and unresolved
   conflicts.
5. **Validate the draft structure.** Generated frontmatter is parsed and
   normalized; names are constrained to portable kebab-case.
6. **Compare the library.** `overlaps` ranks existing pairs; `--compare-dir`
   checks a new candidate against one or more existing libraries. Both are
   lexical review heuristics.
7. **Emit safely.** Writes are contained beneath the chosen directory,
   non-overwriting by default, and staged atomically.

## Validation

See the [real-repository validation status, protocol, and sanitized
artifacts](docs/evaluation.md).

## Privacy and safety

- CLI model use is opt-in through `--provider`.
- Transcript and skill excerpts are redacted for common credential forms
  before provider calls.
- Inputs are explicitly marked as untrusted in model prompts.
- Transcript reads and synthesis batches are bounded.
- Model output is redacted and its YAML frontmatter is rebuilt.
- Skill names reject traversal, absolute paths, and non-portable names.
- Symlink escapes are rejected and writes are atomic.

Redaction is a safety net, not a complete DLP system. Review provider,
retention, source-code, and data-residency policy before sending private
transcripts to any hosted model.

## CLI reference

```text
skilldistill scan [ROOT] [--source auto|claude|cursor]
                  [--min-score 0.5] [--limit 20] [--json]

skilldistill overlaps ROOT [--threshold 0.35] [--limit 50]
                           [--max-skills 500] [--json]

skilldistill distill SESSION [SESSION ...]
                     [--goal TEXT]... [--name NAME]
                     [--base-skill SKILL.md]
                     [--provider offline|openai|anthropic|auto]
                     [--output-dir DIR] [--compare-dir DIR]...
                     [--force]

skilldistill consolidate SKILL.md SKILL.md [SKILL.md ...]
                         [--name NAME] [--output-dir DIR]
                         [--provider offline|openai|anthropic|auto]
                         [--force]
```

`--offline` remains an alias for `--provider offline`.
`--skills-dir` remains a compatibility alias for `--output-dir`.

## Python API

```python
from skilldistill import (
    consolidate_skills,
    distill_sessions,
    find_overlaps,
    parse_session,
    score_session,
    write_skill,
)

sessions = [
    parse_session("traces/retry-api.jsonl"),
    parse_session("traces/retry-worker.jsonl"),
]
selected = [session for session in sessions if score_session(session).score >= 0.5]
overlaps = find_overlaps("skills")

draft = distill_sessions(selected, llm=None, name="retry-workflows")
write_skill(draft, "skill-drafts")

merge = consolidate_skills(
    ["skills/retry/SKILL.md", "skills/backoff/SKILL.md"],
    llm=None,
)
```

With `llm=None`, both calls produce deterministic offline review scaffolds.
Pass any `Callable[[str], str]` for model-backed synthesis, keeping the core
provider-neutral.

## Current limitations

- Success scoring is heuristic unless the transcript contains a real verifier
  result. It is not a substitute for held-out evaluation.
- Related sessions must currently be selected explicitly; automatic
  workflow clustering is not implemented yet.
- Existing-skill overlap discovery is lexical, not semantic; a reviewer must
  decide whether a pair should remain separate.
- The package creates review candidates, not typed patches or a full skill
  directory merge.
- Offline synthesis is an evidence scaffold, not semantic distillation.
- No generic agent runner, evaluator, promotion command, or skill registry is
  included yet.
- Transcript formats are unstable and adapters will need ongoing fixtures.

## Research-driven roadmap

- stable agent-neutral `TraceRecord` and verifier evidence schema;
- automatic clustering of related success and causally explained failure
  trajectories;
- semantic and structural existing-library overlap discovery;
- trajectory-local typed patches with support counts and source hashes;
- hierarchical, conflict-aware patch consolidation;
- create and deepen modes over complete skill packages;
- held-out baseline-versus-candidate evaluator adapters;
- versioned provenance, changelogs, and explicit promotion;
- skill-level memory plus evidence-driven refine, merge, and prune proposals;
- generated `tests/`, `scripts/`, `resources/`, and `references/` packages; and
- additional agent transcript and runtime adapters.

## Development

`skilldistill` is alpha software. Transcript formats, candidate structure, and
Python APIs may change between minor releases.

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check src tests
dunnit verify
```

## Contributing

Focused issues and pull requests are welcome. Please include a minimal
transcript fixture for parser changes, tests for every module change, and keep
the LLM-optional path working. Run the development checks above before opening
a pull request. Do not include private transcripts, credentials, or proprietary
skill content in issues or fixtures.

## Security

Use GitHub's private vulnerability reporting for sensitive findings, or email
`palasht75@gmail.com` if private reporting is unavailable. Use a public issue
for non-sensitive bugs. Never post credentials, private transcripts, or
exploitable details in a public report.

## License

MIT

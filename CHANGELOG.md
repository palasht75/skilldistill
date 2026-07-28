# Changelog

## 0.2.0 - 2026-07-28

- Add multi-trajectory skill synthesis with deterministic offline evidence
  scaffolds and model-backed trajectory-local lesson consolidation.
- Add frozen-base revision candidates without modifying the source skill.
- Add bounded lexical overlap discovery for existing skill libraries and
  repeatable comparison directories for new candidates.
- Add non-destructive consolidation of overlapping existing skills.
- Add Cursor IDE and CLI transcript adapters with tolerant, bounded parsing.
- Add repeatable per-session goal overrides for incomplete agent streams.
- Make cloud use explicit in the CLI; offline mode remains the default.
- Move default output from `skills/` to the non-active `skill-drafts/` review
  directory and provide no install or promotion command.
- Harden transcript redaction, untrusted-content handling, LLM output validation,
  and atomic skill emission.
- Improve success detection and recursive skill deduplication.
- Add a development-only, real-repository evaluation harness with frozen
  candidates, randomized baseline comparisons, usage accounting, sanitized
  artifacts, and hash-bound holdout results.
- Publish the first Click evaluation, which found no new task success or token
  efficiency from its generated candidate and records the result without a
  general performance claim.

## 0.1.0 - 2026-07-28

Initial release.

- Tolerant Claude Code JSONL transcript parser (`~/.claude/projects/**`)
- `skilldistill scan`: rank sessions by success signals with transparent reasons
- `skilldistill distill`: SKILL.md drafts via Anthropic/OpenAI SDKs, or an
  honest offline outline when no LLM is configured
- Dedup warnings against your existing skills directory
- Zero required dependencies beyond pyyaml; LLM SDKs are optional extras

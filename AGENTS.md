# AGENTS.md — skilldistill

Guidance for AI coding agents (Codex, Claude Code, Cursor, etc.) working in this repo.

## What this project is

`skilldistill` mines selected AI-agent trajectories (Claude Code JSONL,
Cursor CLI stream-JSON, and exported Cursor Markdown) for reusable workflow
evidence and distills it into reviewable SKILL.md candidates. Pipeline:
parse transcripts → score evidence heuristically → extract trajectory-local
lessons → synthesize or revise a candidate → compare/consolidate overlap →
write a draft for human review.

## Layout

- `src/skilldistill/transcripts.py` — tolerant agent adapters → `Session`/`Event`
- `src/skilldistill/detect.py` — success scoring with human-readable reasons
- `src/skilldistill/distill.py` — single/multi-trace synthesis → `SkillDraft`
- `src/skilldistill/consolidate.py` — non-destructive existing-skill proposals
- `src/skilldistill/redact.py` — offline credential redaction before egress
- `src/skilldistill/llm.py` — optional Anthropic/OpenAI resolver (env-key gated)
- `src/skilldistill/emitter.py` — atomic, contained draft writes
- `src/skilldistill/dedup.py` — candidate comparison and overlap discovery
- `src/skilldistill/cli.py` — `scan`, `overlaps`, `distill`, and `consolidate`
- `tests/` — pytest; `conftest.py` builds synthetic session fixtures

## Commands

```bash
pip install -e ".[dev]"        # setup (installs dunnit too)
python -m pytest -q            # tests
python -m ruff check src tests # lint
dunnit verify                  # definition of done for this repo
```

## Definition of done

`dunnit verify` must pass. Do not edit `dod.yaml`, `.github/**`, or weaken
tests to make it pass — those diffs fail verification by design.

## Conventions

- Python ≥3.9, `from __future__ import annotations`, modern typing
- Core stays LLM-optional: every feature must work with `llm=None`
- The transcript format is not a stable API — parsers must tolerate unknown
  fields/lines and never raise on malformed input
- Never auto-install or auto-enable a generated skill; drafts are for review
- Every module gets tests; line length 100 (ruff)

## Releasing

Bump version in `pyproject.toml` + `src/skilldistill/__init__.py`, update
`CHANGELOG.md`, push, create a GitHub release tag `vX.Y.Z` — `publish.yml`
publishes to PyPI via trusted publishing.

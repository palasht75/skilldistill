# AGENTS.md — skilldistill

Guidance for AI coding agents (Codex, Claude Code, Cursor, etc.) working in this repo.

## What this project is

`skilldistill` mines AI-agent session transcripts (Claude Code JSONL) for
successful workflows and distills them into SKILL.md playbooks. Pipeline:
parse transcripts → score success heuristically → distill via LLM (or offline
outline) → dedup against existing skills → write draft for human review.

## Layout

- `src/skilldistill/transcripts.py` — tolerant JSONL parser → `Session`/`Event`
- `src/skilldistill/detect.py` — success scoring with human-readable reasons
- `src/skilldistill/distill.py` — LLM prompt + offline fallback → `SkillDraft`
- `src/skilldistill/llm.py` — optional Anthropic/OpenAI resolver (env-key gated)
- `src/skilldistill/emitter.py` — write `skills/<name>/SKILL.md`
- `src/skilldistill/dedup.py` — similarity warnings vs existing skills
- `src/skilldistill/cli.py` — `scan` and `distill` subcommands
- `tests/` — pytest; `conftest.py` builds synthetic session fixtures

## Commands

```bash
pip install -e .[dev]          # setup (installs dunnit too)
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

# skilldistill

**Your agents solve the same problems every week — and remember nothing.**

[![PyPI](https://img.shields.io/pypi/v/skilldistill)](https://pypi.org/project/skilldistill/)
[![CI](https://github.com/palasht75/skilldistill/actions/workflows/ci.yml/badge.svg)](https://github.com/palasht75/skilldistill/actions)
[![Python](https://img.shields.io/pypi/pyversions/skilldistill)](https://pypi.org/project/skilldistill/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Agent skills (SKILL.md playbooks) are how you teach agents procedures — but
everyone writes them by hand, from memory, after the fact. Meanwhile your
`~/.claude/projects/` folder is full of transcripts where the agent *already
figured it out*: the exact commands, the wrong turns, the fix that finally
worked. Research shows distilling skills from execution traces works
([Trace2Skill](https://github.com/Qwen-Applications/Trace2Skill),
[MUSE-Autoskill](https://arxiv.org/html/2605.27366v1)) — skilldistill makes it
a pip install instead of a paper.

```bash
pip install skilldistill
```

## How it works

```text
transcripts ──▶ scan ──▶ rank by success signals ──▶ distill ──▶ SKILL.md draft
                                                        │
                                     dedup vs skills you already have
```

**1. Scan** your Claude Code sessions and rank the ones worth keeping:

```text
$ skilldistill scan ~/.claude/projects
[0.90] ~/.claude/projects/my-api/7f3a...jsonl
       goal: Fix the flaky retry logic in the payment client
       why:  substantial workflow (14 tool calls); low tool error ratio (7%);
             success signals in transcript (tests passed / PASS verdict); clean final summary
[0.70] ~/.claude/projects/my-api/22c1...jsonl
       goal: Add rate limiting to the public endpoints
       why:  substantial workflow (9 tool calls); clean final summary
```

Every score comes with reasons — no black-box filtering.

**2. Distill** a session into a reusable skill draft:

```text
$ export ANTHROPIC_API_KEY=...        # or OPENAI_API_KEY; optional
$ pip install "skilldistill[anthropic]"
$ skilldistill distill ~/.claude/projects/my-api/7f3a...jsonl --skills-dir .claude/skills
wrote .claude/skills/fix-flaky-retry-logic/SKILL.md  (origin: llm)
review the draft before installing it — treat generated skills like third-party code
```

The LLM generalizes the transcript into a procedure: trigger-focused
description, numbered steps with the key commands, and a Pitfalls section
mined from the mistakes the agent made and corrected mid-session.

**No API key? Still works.** `--offline` (or no key configured) emits an
honest outline draft from the recorded tool sequence, clearly marked for
manual polish.

**3. Dedup.** Before writing, skilldistill compares the draft against your
existing skills directory and warns on overlap:

```text
warning: similar existing skill: retry-patterns (78%)
```

## CLI reference

```text
skilldistill scan [ROOT] [--min-score 0.5] [--limit 20] [--json]
skilldistill distill SESSION.jsonl [--skills-dir skills] [--offline] [--force]
```

`scan` defaults to `~/.claude/projects`. `--json` emits machine-readable
rankings for pipelines.

## Python API

```python
from skilldistill import parse_session, score_session, distill, write_skill

session = parse_session("session.jsonl")
if score_session(session).score >= 0.5:
    draft = distill(session, llm=my_llm_fn)   # llm: Callable[[str], str], or None
    write_skill(draft, "skills/")
```

Bring any LLM: `llm` is just a `Callable[[str], str]`. Built-in resolvers use
the `anthropic` or `openai` SDKs when installed
(`SKILLDISTILL_ANTHROPIC_MODEL` / `SKILLDISTILL_OPENAI_MODEL` to override models).

## The flywheel

Pair with [dunnit](https://github.com/palasht75/dunnit): dunnit verdicts in a
transcript ("verdict: PASS") are a strong success signal, so verified sessions
rank highest — you distill skills only from work that was *proven* done, not
just claimed done.

## Security note

Generated skills are instructions your agents will follow. Review drafts like
you'd review third-party code, and consider scanning them (e.g. Snyk Agent
Scan or mcp-scan) before installing — skill supply-chain attacks are
[real and studied](https://snyk.io/blog/toxicskills-malicious-ai-agent-skills-clawhub/).

## Roadmap

Cursor/Codex transcript parsers · per-skill eval stubs · cross-session
clustering (one skill from N similar sessions) · `skilldistill watch` daemon ·
skill update mode (refresh an existing skill from newer sessions).

## License

MIT

# Changelog

## 0.1.0 - 2026-07-28

Initial release.

- Tolerant Claude Code JSONL transcript parser (`~/.claude/projects/**`)
- `skilldistill scan`: rank sessions by success signals with transparent reasons
- `skilldistill distill`: SKILL.md drafts via Anthropic/OpenAI SDKs, or an
  honest offline outline when no LLM is configured
- Dedup warnings against your existing skills directory
- Zero required dependencies beyond pyyaml; LLM SDKs are optional extras

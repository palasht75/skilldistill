"""Distill a session into a SKILL.md draft — with an LLM when available,
with a transparent offline outline when not."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from skilldistill.transcripts import Session

LLMFn = Callable[[str], str]

PROMPT_TEMPLATE = """\
You are distilling a successful AI-agent session into a reusable agent skill.

Write a complete SKILL.md following the Agent Skills format:
- YAML frontmatter with `name` (short, kebab-case) and `description`
  (one sentence starting with "Use this skill when..." so agents know when to
  trigger it).
- Body: a concise, numbered procedure capturing the *generalized* workflow
  (not this session's specifics), key commands, decision points, and a
  "Pitfalls" section for mistakes the transcript shows were made and corrected.
- Generalize: replace project-specific names/paths with <placeholders>.
- Output ONLY the SKILL.md content, starting with `---`.

## Session goal
{goal}

## Tool/command sequence
{trace}

## Final outcome
{outcome}
"""


@dataclass
class SkillDraft:
    name: str
    description: str
    content: str  # full SKILL.md text
    origin: str  # "llm" or "offline"


def _slug(text: str, fallback: str = "distilled-skill") -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())[:5]
    return "-".join(words) or fallback


def _condense(session: Session, limit: int = 40) -> str:
    lines = []
    for ev in session.tool_sequence[:limit]:
        lines.append(f"- {ev.tool}: {ev.text[:140]}")
    if len(session.tool_sequence) > limit:
        lines.append(f"- ... {len(session.tool_sequence) - limit} more tool calls")
    return "\n".join(lines) or "(no tool calls)"


def _frontmatter(content: str) -> tuple[str, str]:
    m = re.search(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    name = desc = ""
    if m:
        for line in m.group(1).splitlines():
            if line.startswith("name:"):
                name = line.split(":", 1)[1].strip().strip("\"'")
            elif line.startswith("description:"):
                desc = line.split(":", 1)[1].strip().strip("\"'")
    return name, desc


def distill(session: Session, llm: LLMFn | None = None) -> SkillDraft:
    goal = session.first_goal[:600] or "(no explicit goal found)"
    if llm is not None:
        prompt = PROMPT_TEMPLATE.format(
            goal=goal,
            trace=_condense(session),
            outcome=session.final_assistant_text[:600] or "(none)",
        )
        content = llm(prompt).strip()
        if not content.startswith("---"):
            content = f"---\nname: {_slug(goal)}\ndescription: {goal[:120]}\n---\n\n{content}"
        name, desc = _frontmatter(content)
        return SkillDraft(
            name=name or _slug(goal), description=desc or goal[:120], content=content, origin="llm"
        )

    # Offline fallback: honest outline, clearly marked as needing review.
    name = _slug(goal)
    desc = "Use this skill when the task resembles " + goal[:110].replace(":", " -")
    steps = "\n".join(
        f"{i + 1}. `{ev.tool}` — {ev.text[:100]}" for i, ev in enumerate(session.tool_sequence[:25])
    )
    content = f"""---
name: {name}
description: "{desc}"
---

# {name.replace("-", " ").title()}

> Drafted offline by skilldistill (no LLM available). The tool sequence below
> is the recorded workflow; generalize wording and prune specifics by hand,
> or re-run with an LLM configured for a polished draft.

## Original goal

{goal}

## Recorded procedure

{steps or "(no tool calls recorded)"}

## Pitfalls

- Review the original session for corrected mistakes and add them here.
"""
    return SkillDraft(name=name, description=desc, content=content, origin="offline")

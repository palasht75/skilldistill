"""Warn when a draft duplicates a skill you already have."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from skilldistill.distill import SkillDraft


@dataclass
class SimilarSkill:
    path: Path
    name: str
    similarity: float


def _meta(skill_md: Path) -> str:
    text = skill_md.read_text(encoding="utf-8", errors="replace")[:2000]
    m = re.search(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    return (m.group(1) if m else text).lower()


def find_similar(
    draft: SkillDraft, skills_dir: Path | str, threshold: float = 0.55
) -> list[SimilarSkill]:
    skills_dir = Path(skills_dir)
    if not skills_dir.exists():
        return []
    needle = f"{draft.name} {draft.description}".lower()
    out = []
    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        ratio = SequenceMatcher(None, needle, _meta(skill_md)).ratio()
        if skill_md.parent.name == draft.name:
            ratio = max(ratio, 1.0)
        if ratio >= threshold:
            out.append(
                SimilarSkill(path=skill_md, name=skill_md.parent.name, similarity=round(ratio, 2))
            )
    return sorted(out, key=lambda s: -s.similarity)

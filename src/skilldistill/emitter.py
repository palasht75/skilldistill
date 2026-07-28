"""Write skill drafts to a skills directory."""

from __future__ import annotations

from pathlib import Path

from skilldistill.distill import SkillDraft


class SkillExistsError(FileExistsError):
    pass


def write_skill(draft: SkillDraft, skills_dir: Path | str, force: bool = False) -> Path:
    skills_dir = Path(skills_dir)
    target = skills_dir / draft.name / "SKILL.md"
    if target.exists() and not force:
        raise SkillExistsError(f"{target} already exists (use force to overwrite)")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(draft.content, encoding="utf-8")
    return target

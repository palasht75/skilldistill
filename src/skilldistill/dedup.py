"""Warn when a draft duplicates a skill you already have."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from skilldistill.distill import SkillDraft
from skilldistill.redact import redact_text

MAX_SKILL_PREVIEW_CHARS = 12_000
MAX_LIBRARY_SKILLS = 500
MAX_SIGNATURE_TERMS = 240
_FRONTMATTER = re.compile(
    r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)",
    re.DOTALL,
)
_TERM = re.compile(r"[a-z0-9][a-z0-9_-]{2,}")
_STOPWORDS = {
    "agent",
    "and",
    "are",
    "for",
    "from",
    "into",
    "redacted",
    "skill",
    "steps",
    "that",
    "the",
    "then",
    "this",
    "use",
    "when",
    "with",
    "workflow",
}


@dataclass
class SimilarSkill:
    path: Path
    name: str
    similarity: float


@dataclass(frozen=True)
class SkillOverlap:
    left_path: Path
    left_name: str
    right_path: Path
    right_name: str
    similarity: float
    shared_terms: tuple[str, ...]


def _preview(skill_md: Path, max_chars: int = MAX_SKILL_PREVIEW_CHARS) -> str:
    try:
        with skill_md.open("r", encoding="utf-8", errors="replace") as handle:
            return handle.read(max_chars)
    except OSError:
        return ""


def _meta(skill_md: Path) -> str:
    text = _preview(skill_md, max_chars=2_000)
    match = _FRONTMATTER.match(text)
    return (match.group(1) if match else text).lower()


def _terms(text: str) -> frozenset[str]:
    output = []
    seen = set()
    for term in _TERM.findall(text.lower()):
        normalized = term.replace("_", "-").strip("-")
        if normalized in _STOPWORDS or normalized in seen:
            continue
        seen.add(normalized)
        output.append(normalized)
        if len(output) >= MAX_SIGNATURE_TERMS:
            break
    return frozenset(output)


def find_similar(
    draft: SkillDraft, skills_dir: Path | str, threshold: float = 0.55
) -> list[SimilarSkill]:
    skills_dir = Path(skills_dir)
    if not skills_dir.exists():
        return []
    needle = f"{draft.name} {draft.description}".lower()
    out = []
    for skill_md in sorted(skills_dir.rglob("SKILL.md")):
        ratio = SequenceMatcher(None, needle, _meta(skill_md)).ratio()
        if skill_md.parent.name == draft.name:
            ratio = max(ratio, 1.0)
        if ratio >= threshold:
            out.append(
                SimilarSkill(path=skill_md, name=skill_md.parent.name, similarity=round(ratio, 2))
            )
    return sorted(out, key=lambda s: -s.similarity)


def find_overlaps(
    skills_dir: Path | str,
    *,
    threshold: float = 0.35,
    limit: int = 100,
    max_skills: int = MAX_LIBRARY_SKILLS,
) -> list[SkillOverlap]:
    """Find lexical overlap candidates in an existing skill library.

    This is a bounded discovery heuristic, not evidence that two skills should
    merge. It reads only a preview of each SKILL.md and never modifies files.
    """

    if not 0 <= threshold <= 1:
        raise ValueError("threshold must be between 0 and 1")
    if limit < 1:
        raise ValueError("limit must be positive")
    if max_skills < 2:
        raise ValueError("max_skills must be at least 2")

    root = Path(skills_dir).expanduser()
    if not root.is_dir():
        raise ValueError(f"skills directory not found: {root}")
    paths = sorted(path for path in root.rglob("SKILL.md") if path.is_file())
    if len(paths) > max_skills:
        raise ValueError(
            f"found {len(paths)} skills; narrow the directory or raise "
            f"max_skills above {max_skills}"
        )

    records = []
    for path in paths:
        signature = _terms(redact_text(_preview(path)))
        if signature:
            records.append((path, path.parent.name, signature))

    overlaps = []
    for left_index, (left_path, left_name, left_terms) in enumerate(records):
        for right_path, right_name, right_terms in records[left_index + 1 :]:
            shared = left_terms & right_terms
            if len(shared) < 2:
                continue
            similarity = 2 * len(shared) / (len(left_terms) + len(right_terms))
            if similarity < threshold:
                continue
            overlaps.append(
                SkillOverlap(
                    left_path=left_path,
                    left_name=left_name,
                    right_path=right_path,
                    right_name=right_name,
                    similarity=round(similarity, 2),
                    shared_terms=tuple(sorted(shared)[:20]),
                )
            )
    return sorted(
        overlaps,
        key=lambda overlap: (
            -overlap.similarity,
            str(overlap.left_path),
            str(overlap.right_path),
        ),
    )[:limit]

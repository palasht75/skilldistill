"""skilldistill — turn selected agent trajectories into reviewable skill candidates.

skilldistill normalizes agent transcripts, ranks reusable evidence, and
distills one or more trajectories into SKILL.md candidates for human review.
"""

from skilldistill.consolidate import SkillSource, consolidate_skills, load_skill_source
from skilldistill.dedup import SkillOverlap, find_overlaps
from skilldistill.detect import SessionScore, score_session
from skilldistill.distill import SkillDraft, distill, distill_sessions
from skilldistill.emitter import write_skill
from skilldistill.transcripts import Event, Session, find_sessions, parse_session

__version__ = "0.2.0"
__all__ = [
    "Event",
    "Session",
    "SessionScore",
    "SkillDraft",
    "SkillOverlap",
    "SkillSource",
    "__version__",
    "consolidate_skills",
    "distill",
    "distill_sessions",
    "find_overlaps",
    "find_sessions",
    "load_skill_source",
    "parse_session",
    "score_session",
    "write_skill",
]

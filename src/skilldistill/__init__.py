"""skilldistill — turn successful agent sessions into reusable skills.

Your agents solve the same problems every week and forget everything.
skilldistill mines session transcripts (Claude Code JSONL first), finds the
runs that actually succeeded, and distills them into SKILL.md playbooks —
deduplicated against the skills you already have, drafted for human review.
"""

from skilldistill.detect import SessionScore, score_session
from skilldistill.distill import SkillDraft, distill
from skilldistill.emitter import write_skill
from skilldistill.transcripts import Event, Session, find_sessions, parse_session

__version__ = "0.1.0"
__all__ = [
    "Event",
    "Session",
    "SessionScore",
    "SkillDraft",
    "__version__",
    "distill",
    "find_sessions",
    "parse_session",
    "score_session",
    "write_skill",
]

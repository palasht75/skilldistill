"""Score how likely a session represents a *successful* workflow worth
distilling. Heuristic and transparent: every point comes with a reason."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from skilldistill.transcripts import Session

SUCCESS_HINTS = re.compile(
    r"\b(\d+ passed|all tests pass|tests? pass|build succeeded|verdict: PASS|"
    r"deployed|published|LINT.?CLEAN|exited 0)\b",
    re.IGNORECASE,
)
FAILURE_HINTS = re.compile(
    r"\b(failed|error|traceback|exception|cannot|unable to|verdict: FAIL)\b", re.IGNORECASE
)


@dataclass
class SessionScore:
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)


def score_session(session: Session) -> SessionScore:
    s = SessionScore()
    results = [ev for ev in session.events if ev.kind == "tool_result"]
    tools = session.tool_sequence

    if len(tools) >= 3:
        s.score += 0.2
        s.reasons.append(f"substantial workflow ({len(tools)} tool calls)")
    if results:
        error_ratio = sum(1 for r in results if r.is_error) / len(results)
        if error_ratio < 0.2:
            s.score += 0.2
            s.reasons.append(f"low tool error ratio ({error_ratio:.0%})")
    body = " ".join(ev.text for ev in session.events)
    if SUCCESS_HINTS.search(body):
        s.score += 0.3
        s.reasons.append("success signals in transcript (tests passed / PASS verdict)")
    final = session.final_assistant_text
    if final and not FAILURE_HINTS.search(final[-300:]):
        s.score += 0.2
        s.reasons.append("clean final summary")
    if "dunnit" in body and "PASS" in body:
        s.score += 0.1
        s.reasons.append("dunnit verification passed")
    s.score = round(min(s.score, 1.0), 2)
    return s

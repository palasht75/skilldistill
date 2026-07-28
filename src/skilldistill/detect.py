"""Score how likely a session represents a *successful* workflow worth
distilling. Heuristic and transparent: every point comes with a reason."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from skilldistill.transcripts import Session

SUCCESS_HINTS = re.compile(
    r"\b(\d+ (?:tests? )?passed|all tests pass|tests? pass(?:ed)?|build succeeded|verdict: PASS|"
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
        s.score += 0.15
        s.reasons.append(f"substantial workflow ({len(tools)} tool calls)")
    if results:
        error_ratio = sum(1 for r in results if r.is_error) / len(results)
        if error_ratio < 0.2:
            s.score += 0.2
            s.reasons.append(f"low tool error ratio ({error_ratio:.0%})")
    result_text = " ".join(event.text for event in results)
    if SUCCESS_HINTS.search(result_text):
        s.score += 0.35
        s.reasons.append("recorded success signal in tool result (tests/build/PASS)")
    final = session.final_assistant_text
    if final and not session.terminal_is_error and not FAILURE_HINTS.search(final[-300:]):
        s.score += 0.15
        s.reasons.append("clean final summary")
        if SUCCESS_HINTS.search(final):
            s.score += 0.1
            s.reasons.append("assistant-reported success (not independent verification)")
    if "dunnit" in result_text and "PASS" in result_text:
        s.score += 0.1
        s.reasons.append("dunnit PASS recorded in tool result")
    recent_results = " ".join(event.text[-300:] for event in results[-2:])
    if (
        session.terminal_is_error
        or
        any(event.is_error for event in results[-2:])
        or FAILURE_HINTS.search(recent_results)
        or FAILURE_HINTS.search(final[-300:])
    ):
        s.score -= 0.2
        if session.terminal_is_error:
            s.reasons.append("terminal agent result reported failure")
        else:
            s.reasons.append("unresolved failure signal near session end")
    s.score = round(max(0.0, min(s.score, 1.0)), 2)
    return s

from pathlib import Path

from skilldistill.detect import score_session
from skilldistill.transcripts import Event, Session, parse_session


def test_good_session_outscores_bad(good_session, bad_session):
    good = score_session(parse_session(good_session))
    bad = score_session(parse_session(bad_session))
    assert good.score > bad.score
    assert good.score >= 0.5
    assert any("recorded success signal" in r for r in good.reasons)


def test_user_request_to_make_tests_pass_is_not_success_evidence():
    session = Session(
        path=Path("synthetic"),
        events=[
            Event(kind="user", text="Make all tests pass and fix every error"),
            Event(kind="tool_use", tool="read"),
            Event(kind="tool_result", text="source", is_error=False),
            Event(kind="tool_use", tool="edit"),
            Event(kind="tool_result", text="changed", is_error=False),
            Event(kind="tool_use", tool="shell"),
            Event(kind="tool_result", text="fatal build problem", is_error=True),
            Event(kind="assistant", text="Stopping for now."),
        ],
    )

    score = score_session(session)

    assert score.score < 0.5
    assert not any("recorded success" in reason for reason in score.reasons)
    assert any("unresolved failure" in reason for reason in score.reasons)


def test_cursor_terminal_failure_is_not_scored_as_a_clean_summary():
    session = Session(
        path=Path("failed.jsonl"),
        events=[Event(kind="assistant", text="Stopped")],
        source="cursor",
        terminal_subtype="error",
        terminal_is_error=True,
    )

    score = score_session(session)

    assert "clean final summary" not in score.reasons
    assert "terminal agent result reported failure" in score.reasons

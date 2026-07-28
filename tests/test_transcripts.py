from skilldistill.transcripts import parse_session


def test_parse_tolerant_and_ordered(good_session):
    s = parse_session(good_session)
    assert s.first_goal.startswith("Fix the flaky retry")
    tools = [ev.tool for ev in s.tool_sequence]
    assert tools == ["Read", "Edit", "Bash"]
    assert "12 passed" in s.final_assistant_text or "Done" in s.final_assistant_text


def test_parse_skips_junk_lines(good_session):
    s = parse_session(good_session)  # includes a non-JSON line
    assert len(s.events) > 0

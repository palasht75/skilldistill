import json

from skilldistill.transcripts import find_sessions, parse_session


def test_parse_tolerant_and_ordered(good_session):
    s = parse_session(good_session)
    assert s.first_goal.startswith("Fix the flaky retry")
    tools = [ev.tool for ev in s.tool_sequence]
    assert tools == ["Read", "Edit", "Bash"]
    assert "12 passed" in s.final_assistant_text or "Done" in s.final_assistant_text


def test_parse_skips_junk_lines(good_session):
    s = parse_session(good_session)  # includes a non-JSON line
    assert len(s.events) > 0


def test_parse_skips_non_string_event_type(tmp_path):
    path = tmp_path / "malformed.jsonl"
    path.write_text(
        '{"type":[]}\n'
        '{"type":"user","message":{"content":"Valid goal"}}\n',
        encoding="utf-8",
    )

    session = parse_session(path)

    assert session.first_goal == "Valid goal"


def test_parse_cursor_cli_stream_json(tmp_path):
    lines = [
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "I will inspect the project."}],
            },
            "session_id": "cursor-session",
            "model": "composer",
        },
        {
            "type": "tool_call",
            "subtype": "started",
            "call_id": "call-1",
            "tool_call": {"readToolCall": {"args": {"path": "README.md"}}},
            "session_id": "cursor-session",
        },
        {
            "type": "tool_call",
            "subtype": "completed",
            "call_id": "call-1",
            "tool_call": {
                "readToolCall": {
                    "args": {"path": "README.md"},
                    "result": {"success": {"content": "# Project"}},
                }
            },
            "session_id": "cursor-session",
        },
        {
            "type": "result",
            "subtype": "success",
            "duration_ms": 1234,
            "is_error": False,
            "result": "Done. Tests passed.",
            "session_id": "cursor-session",
        },
    ]
    path = tmp_path / "cursor.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")

    session = parse_session(path)

    assert session.source == "cursor"
    assert session.session_id == "cursor-session"
    assert session.model == "composer"
    assert session.duration_ms == 1234
    assert [event.tool for event in session.tool_sequence] == ["read"]
    assert "Tests passed" in session.final_assistant_text
    results = [event for event in session.events if event.kind == "tool_result"]
    assert len(results) == 1 and not results[0].is_error


def test_parse_cursor_exported_markdown(tmp_path):
    path = tmp_path / "export.md"
    path.write_text(
        """# Fix retry behavior

- **Workspace:** /project

---

## User

Fix the retry behavior and run the tests.

---

## Assistant

Implemented bounded retries. 12 tests passed.
""",
        encoding="utf-8",
    )

    session = parse_session(path)

    assert session.source == "cursor"
    assert session.first_goal == "Fix the retry behavior and run the tests."
    assert session.final_assistant_text == "Implemented bounded retries. 12 tests passed."


def test_parse_cursor_markdown_preserves_fenced_roles_and_message_rules(tmp_path):
    path = tmp_path / "export.md"
    path.write_text(
        """## User

Show this example:

```markdown
## Assistant

Not a real role.
```

---
Keep this horizontal rule.

---

## Assistant

Done.
""",
        encoding="utf-8",
    )

    session = parse_session(path)

    assert len(session.events) == 2
    assert "## Assistant" in session.first_goal
    assert "---\nKeep this horizontal rule" in session.first_goal
    assert session.final_assistant_text == "Done."


def test_parse_cursor_stream_tolerates_unknown_and_failed_tool(tmp_path):
    path = tmp_path / "cursor.jsonl"
    path.write_text(
        "\n".join(
            [
                "not-json",
                json.dumps({"type": "future_event", "unknown": True}),
                json.dumps(
                    {
                        "type": "tool_call",
                        "subtype": "failed",
                        "call_id": "call-2",
                        "tool_call": {
                            "shellToolCall": {
                                "args": {"command": "pytest"},
                                "result": {"failure": {"message": "failed"}},
                            }
                        },
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    session = parse_session(path)

    assert [event.tool for event in session.tool_sequence] == ["shell"]
    result = next(event for event in session.events if event.kind == "tool_result")
    assert result.is_error


def test_parse_cursor_terminal_failure_is_preserved(tmp_path):
    path = tmp_path / "failed.jsonl"
    path.write_text(
        json.dumps(
            {
                "type": "result",
                "subtype": "error",
                "is_error": True,
                "result": "Stopped",
            }
        ),
        encoding="utf-8",
    )

    session = parse_session(path)

    assert session.terminal_subtype == "error"
    assert session.terminal_is_error
    assert session.final_assistant_text == "Stopped"


def test_parse_session_respects_size_limit(tmp_path):
    path = tmp_path / "bounded.jsonl"
    first = json.dumps(
        {
            "type": "user",
            "message": {"content": [{"type": "text", "text": "First goal"}]},
        }
    )
    second = json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "Late response"}]},
        }
    )
    path.write_text(f"{first}\n{second}\n", encoding="utf-8")

    session = parse_session(path, max_bytes=len(first.encode("utf-8")) + 1)

    assert session.first_goal == "First goal"
    assert not session.final_assistant_text


def test_parse_session_does_not_read_an_oversized_single_line(tmp_path):
    path = tmp_path / "oversized.jsonl"
    path.write_bytes(b'{"type":"user","padding":"' + b"x" * 10_000 + b'"}\n')

    session = parse_session(path, max_bytes=100)

    assert session.events == []


def test_parse_markdown_size_limit_is_measured_in_bytes(tmp_path):
    path = tmp_path / "bounded.md"
    path.write_text(
        "## User\n\nGoal\n\n## Assistant\n\n" + "\N{SNOWMAN}" * 1000,
        encoding="utf-8",
    )

    session = parse_session(path, max_bytes=64)

    assert session.first_goal == "Goal"
    assert len(session.final_assistant_text.encode("utf-8")) <= 64


def test_find_cursor_sessions_includes_chat_markdown_but_not_regular_docs(tmp_path):
    chat = tmp_path / "chat.md"
    chat.write_text("## User\n\nHelp\n\n## Assistant\n\nDone", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Documentation", encoding="utf-8")
    stream = tmp_path / "run.jsonl"
    stream.write_text("{}\n", encoding="utf-8")

    found = set(find_sessions(tmp_path, source="cursor"))

    assert found == {chat, stream}
    assert find_sessions(tmp_path, source="claude") == [stream]


def test_find_cursor_sessions_ignores_role_examples_inside_code_fences(tmp_path):
    documentation = tmp_path / "format.md"
    documentation.write_text(
        "# Format\n\n```markdown\n## User\n\nHello\n\n## Assistant\n\nHi\n```\n",
        encoding="utf-8",
    )

    assert find_sessions(tmp_path, source="cursor") == []

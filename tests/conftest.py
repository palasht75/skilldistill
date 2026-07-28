import json
from pathlib import Path

import pytest


def _line(kind, content):
    return json.dumps({"type": kind, "message": {"role": kind, "content": content}})


@pytest.fixture
def good_session(tmp_path: Path) -> Path:
    """A successful session: goal -> tools -> tests pass -> clean summary."""
    lines = [
        _line("user", "Fix the flaky retry logic in the payment client"),
        _line("assistant", [{"type": "text", "text": "Looking at the retry code."}]),
        _line("assistant", [{"type": "tool_use", "name": "Read", "input": {"file_path": "pay.py"}}]),
        _line("user", [{"type": "tool_result", "content": "def retry(): ...", "is_error": False}]),
        _line("assistant", [{"type": "tool_use", "name": "Edit", "input": {"file_path": "pay.py"}}]),
        _line("user", [{"type": "tool_result", "content": "ok", "is_error": False}]),
        _line("assistant", [{"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q"}}]),
        _line("user", [{"type": "tool_result", "content": "12 passed in 0.4s", "is_error": False}]),
        "not json at all {{{",
        _line("assistant", [{"type": "text", "text": "Done. Retries use exponential backoff; 12 passed."}]),
    ]
    p = tmp_path / "good.jsonl"
    p.write_text("\n".join(lines))
    return p


@pytest.fixture
def bad_session(tmp_path: Path) -> Path:
    lines = [
        _line("user", "Fix the build"),
        _line("assistant", [{"type": "tool_use", "name": "Bash", "input": {"command": "make"}}]),
        _line("user", [{"type": "tool_result", "content": "error: undefined symbol", "is_error": True}]),
        _line("assistant", [{"type": "text", "text": "I was unable to fix the error, giving up."}]),
    ]
    p = tmp_path / "bad.jsonl"
    p.write_text("\n".join(lines))
    return p

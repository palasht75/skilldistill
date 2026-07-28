from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluation.codex_adapter import (
    TokenUsage,
    _usage_from,
    adapt_codex_jsonl,
    parse_codex_jsonl,
    sanitize_text,
)


def _line(record_type: str, **values) -> str:
    return json.dumps({"type": record_type, **values})


def test_adapts_completed_codex_trace_and_deduplicates_item_ids():
    lines = [
        _line("thread.started", thread_id="thread-secret-id"),
        _line("turn.started"),
        _line(
            "item.started",
            item={
                "id": "tool-secret-id",
                "type": "command_execution",
                "command": "python -m pytest -q",
                "status": "in_progress",
            },
        ),
        _line(
            "item.started",
            item={
                "id": "tool-secret-id",
                "type": "command_execution",
                "command": "python -m pytest -q",
                "status": "in_progress",
            },
        ),
        _line(
            "item.completed",
            item={
                "id": "tool-secret-id",
                "type": "command_execution",
                "command": "python -m pytest -q",
                "aggregated_output": "8 passed",
                "exit_code": 0,
                "status": "completed",
            },
        ),
        _line(
            "item.completed",
            item={
                "id": "assistant-secret-id",
                "type": "agent_message",
                "text": "Fixed and verified.",
            },
        ),
        _line(
            "turn.completed",
            usage={
                "input_tokens": 120,
                "input_tokens_details": {
                    "cached_tokens": 45,
                    "cache_write_tokens": 7,
                },
                "output_tokens": 30,
                "output_tokens_details": {"reasoning_tokens": 11},
                "total_tokens": 150,
            },
        ),
    ]

    trace = adapt_codex_jsonl("\n".join(lines), goal="Fix normalization", model="test-model")

    assert trace.eligible
    assert trace.turn_count == 1
    assert trace.completed_tool_count == 1
    assert [event.kind for event in trace.session.events] == [
        "tool_use",
        "tool_result",
        "assistant",
    ]
    assert trace.usage == TokenUsage(
        input_tokens=120,
        cached_tokens=45,
        cache_write_tokens=7,
        output_tokens=30,
        reasoning_tokens=11,
        total_tokens=150,
    )
    assert trace.usage.fresh_input_tokens == 75
    assert trace.requested_model == "test-model"
    assert trace.session.model == ""
    assert trace.to_artifact()["returned_model"] is None


def test_observed_model_is_distinct_from_requested_override():
    trace = adapt_codex_jsonl(
        [
            _line("thread.started", model="returned-model"),
            _line("turn.completed"),
        ],
        model="requested-model",
    )

    assert trace.requested_model == "requested-model"
    assert trace.session.model == "returned-model"
    assert trace.to_artifact()["returned_model"] == "returned-model"


def test_completed_item_without_started_item_still_has_call_and_result():
    trace = adapt_codex_jsonl(
        "\n".join(
            [
                _line("turn.started"),
                _line(
                    "item.completed",
                    item={
                        "id": "command-1",
                        "type": "command_execution",
                        "command": "pytest",
                        "aggregated_output": "failed",
                        "exit_code": 2,
                        "status": "failed",
                    },
                ),
                _line(
                    "item.completed",
                    item={"id": "message-1", "type": "agent_message", "text": "Unable."},
                ),
                _line("turn.failed"),
            ]
        )
    )

    assert [event.kind for event in trace.session.events] == [
        "tool_use",
        "tool_result",
        "assistant",
    ]
    assert trace.session.events[1].is_error
    assert not trace.eligible
    assert trace.eligibility_reasons == ("missing successful terminal event",)


@pytest.mark.parametrize(
    ("records", "reason"),
    [
        (
            [
                _line(
                    "item.completed",
                    item={"id": "a", "type": "agent_message", "text": "Done"},
                ),
                _line("turn.completed"),
            ],
            "missing completed tool call",
        ),
        (
            [
                _line(
                    "item.completed",
                    item={
                        "id": "t",
                        "type": "command_execution",
                        "command": "true",
                        "exit_code": 0,
                    },
                ),
                _line("turn.completed"),
            ],
            "missing completed assistant message",
        ),
    ],
)
def test_eligibility_requires_assistant_tool_and_successful_terminal(records, reason):
    trace = adapt_codex_jsonl(records)

    assert not trace.eligible
    assert reason in trace.eligibility_reasons


def test_reasoning_ids_secrets_and_machine_paths_are_not_publishable_canary():
    secret = "sk-proj-" + "x" * 24
    wsl_path = "/mnt/c/Users/alice/project/src/core.py"
    windows_path = r"C:\Users\alice\project\src\core.py"
    lines = [
        _line("thread.started", thread_id="thread-canary-123"),
        _line("turn.started"),
        _line(
            "item.completed",
            item={
                "id": "reasoning-canary-123",
                "type": "reasoning",
                "text": f"private chain {secret} {wsl_path}",
            },
        ),
        _line(
            "item.completed",
            item={
                "id": "tool-canary-123",
                "type": "command_execution",
                "command": f"OPENAI_API_KEY={secret} pytest {windows_path}",
                "aggregated_output": f"read {wsl_path}",
                "exit_code": 0,
            },
        ),
        _line(
            "item.completed",
            item={
                "id": "message-canary-123",
                "type": "agent_message",
                "text": f"Verified {wsl_path}",
            },
        ),
        _line("turn.completed"),
    ]

    artifact = json.dumps(adapt_codex_jsonl(lines).to_artifact())

    for canary in (
        secret,
        wsl_path,
        windows_path,
        "private chain",
        "thread-canary-123",
        "reasoning-canary-123",
        "tool-canary-123",
        "message-canary-123",
    ):
        assert canary not in artifact
    assert "[REDACTED]" in artifact
    assert "<local-path>" in artifact


def test_usage_accepts_nullable_mapping_and_attribute_shapes():
    mapping_usage = {
        "input_tokens": None,
        "input_tokens_details": None,
        "output_tokens": "12",
        "output_tokens_details": {"reasoning_tokens": None},
        "total_tokens": 12,
    }
    attribute_usage = SimpleNamespace(
        input_tokens=100,
        input_tokens_details=SimpleNamespace(
            cached_tokens=25,
            cache_write_tokens=5,
        ),
        output_tokens=9,
        output_tokens_details=SimpleNamespace(reasoning_tokens=4),
        total_tokens=109,
    )

    assert _usage_from(mapping_usage) == TokenUsage(
        output_tokens=12,
        total_tokens=12,
    )
    parsed = _usage_from(attribute_usage)
    assert parsed.cached_tokens == 25
    assert parsed.cache_write_tokens == 5
    assert parsed.reasoning_tokens == 4
    assert parsed.fresh_input_tokens == 75


def test_malformed_unknown_and_partial_records_are_ignored():
    trace = adapt_codex_jsonl(
        [
            "not json",
            "[]",
            _line("future.record", secret="ignored"),
            _line("item.completed", item=None),
            _line("item.completed", item={"type": "reasoning", "text": "hidden"}),
        ]
    )

    assert trace.session.events == []
    assert not trace.eligible
    assert len(trace.eligibility_reasons) == 3


def test_parse_is_bounded_tolerant_and_never_exposes_input_path(tmp_path: Path):
    trace_path = tmp_path / "private-user" / "trace.jsonl"
    trace_path.parent.mkdir()
    trace_path.write_text(
        _line(
            "item.completed",
            item={"id": "m", "type": "agent_message", "text": str(trace_path)},
        ),
        encoding="utf-8",
    )

    trace = parse_codex_jsonl(trace_path, max_bytes=10_000)

    assert str(tmp_path) not in trace.session.final_assistant_text
    assert trace.session.path == Path("<codex-trace>")
    assert not parse_codex_jsonl(tmp_path / "missing.jsonl").eligible
    with pytest.raises(ValueError, match="max_bytes must be positive"):
        parse_codex_jsonl(trace_path, max_bytes=0)


def test_absent_usage_is_null_and_oversized_trace_fails_closed(tmp_path: Path):
    trace = adapt_codex_jsonl(_line("turn.completed"))

    assert trace.usage.to_dict() == {
        "input_tokens": None,
        "cached_tokens": None,
        "cache_write_tokens": None,
        "fresh_input_tokens": None,
        "output_tokens": None,
        "reasoning_tokens": None,
        "total_tokens": None,
    }

    trace_path = tmp_path / "oversized.jsonl"
    trace_path.write_text(_line("turn.completed") + (" " * 100), encoding="utf-8")
    bounded = parse_codex_jsonl(trace_path, max_bytes=10)

    assert bounded.truncated
    assert not bounded.eligible
    assert "trace exceeded byte limit" in bounded.eligibility_reasons


def test_sanitize_text_handles_explicit_posix_wsl_and_windows_paths():
    text = (
        "/home/alice/repo/file.py "
        "/mnt/c/Users/alice/repo/file.py "
        r"C:\Users\alice\repo\file.py "
        "/custom/evaluation/root/file.py"
    )

    result = sanitize_text(text, local_paths=["/custom/evaluation/root"])

    assert "alice" not in result
    assert "/custom/evaluation/root" not in result
    assert result.count("<local-path>") == 4

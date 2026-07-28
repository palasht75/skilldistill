"""Normalize ``codex exec --json`` output for development-only evaluation.

Codex JSONL is not a stable public API. This adapter is deliberately tolerant:
malformed and unknown records are skipped, unavailable usage fields remain
null, and only completed, publishable evidence is retained. Raw logs should
remain local and uncommitted.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from skilldistill.redact import redact_text
from skilldistill.transcripts import Event, Session

_WINDOWS_PATH = re.compile(
    r"(?i)(?<![a-z0-9])(?:[a-z]:[\\/]|\\\\[^\\/\s]+[\\/])"
    r"[^\s\"'<>|]+"
)
_POSIX_PATH = re.compile(
    r"(?<![:a-zA-Z0-9])/(?:home|Users|mnt|tmp|private|var|opt|workspace|workspaces)"
    r"(?:/[^\s\"'<>]+)+"
)


@dataclass(frozen=True)
class TokenUsage:
    """Provider usage captured from one or more completed Codex turns."""

    input_tokens: int | None = None
    cached_tokens: int | None = None
    cache_write_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None

    @property
    def fresh_input_tokens(self) -> int | None:
        """Input tokens not served from the provider cache."""

        if self.input_tokens is None:
            return None
        return max(self.input_tokens - (self.cached_tokens or 0), 0)

    def __add__(self, other: TokenUsage) -> TokenUsage:
        if not isinstance(other, TokenUsage):
            return NotImplemented

        def add(left: int | None, right: int | None) -> int | None:
            if left is None:
                return right
            if right is None:
                return left
            return left + right

        return TokenUsage(
            input_tokens=add(self.input_tokens, other.input_tokens),
            cached_tokens=add(self.cached_tokens, other.cached_tokens),
            cache_write_tokens=add(
                self.cache_write_tokens, other.cache_write_tokens
            ),
            output_tokens=add(self.output_tokens, other.output_tokens),
            reasoning_tokens=add(self.reasoning_tokens, other.reasoning_tokens),
            total_tokens=add(self.total_tokens, other.total_tokens),
        )

    def to_dict(self) -> dict[str, int | None]:
        return {
            "input_tokens": self.input_tokens,
            "cached_tokens": self.cached_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "fresh_input_tokens": self.fresh_input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass
class CodexTrace:
    """A normalized trace plus the minimum metadata needed by the study."""

    session: Session
    requested_model: str | None = None
    usage: TokenUsage = field(default_factory=TokenUsage)
    turn_count: int = 0
    completed_tool_count: int = 0
    successful_terminal: bool = False
    truncated: bool = False

    @property
    def eligibility_reasons(self) -> tuple[str, ...]:
        reasons = []
        if not self.session.final_assistant_text:
            reasons.append("missing completed assistant message")
        if self.completed_tool_count < 1:
            reasons.append("missing completed tool call")
        if not self.successful_terminal:
            reasons.append("missing successful terminal event")
        if self.truncated:
            reasons.append("trace exceeded byte limit")
        return tuple(reasons)

    @property
    def eligible(self) -> bool:
        """Whether this trace is suitable as source evidence."""

        return not self.eligibility_reasons

    def to_artifact(self) -> dict[str, Any]:
        """Return the allowlisted, identifier-free representation to publish."""

        return {
            "schema_version": 1,
            "source": "codex",
            "goal": self.session.first_goal,
            "model": self.session.model or None,
            "requested_model": self.requested_model,
            "returned_model": self.session.model or None,
            "turn_count": self.turn_count,
            "completed_tool_count": self.completed_tool_count,
            "successful_terminal": self.successful_terminal,
            "truncated": self.truncated,
            "eligible": self.eligible,
            "eligibility_reasons": list(self.eligibility_reasons),
            "usage": self.usage.to_dict(),
            "events": [
                {
                    "kind": event.kind,
                    "text": event.text,
                    "tool": event.tool,
                    "is_error": event.is_error,
                }
                for event in self.session.events
            ],
        }


def sanitize_text(text: str, *, local_paths: Iterable[str | Path] = ()) -> str:
    """Redact credentials and machine-specific absolute paths from text."""

    if not isinstance(text, str):
        raise TypeError("text must be a string")
    sanitized = redact_text(text)
    explicit_paths = sorted(
        {
            str(path)
            for path in local_paths
            if str(path) and str(path) not in {".", "/"}
        },
        key=len,
        reverse=True,
    )
    for value in explicit_paths:
        sanitized = sanitized.replace(value, "<local-path>")
        sanitized = sanitized.replace(value.replace("\\", "/"), "<local-path>")
        sanitized = sanitized.replace(value.replace("/", "\\"), "<local-path>")
    sanitized = _WINDOWS_PATH.sub("<local-path>", sanitized)
    return _POSIX_PATH.sub("<local-path>", sanitized)


def _value(obj: object, name: str, default: object = None) -> object:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _optional_nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return max(int(value), 0)
    except (TypeError, ValueError, OverflowError):
        return None


def _usage_from(value: object) -> TokenUsage:
    if value is None:
        return TokenUsage()
    input_details = _value(value, "input_tokens_details", {}) or {}
    output_details = _value(value, "output_tokens_details", {}) or {}
    cached = _value(input_details, "cached_tokens")
    if cached is None:
        cached = _value(value, "cached_input_tokens")
    cache_write = _value(input_details, "cache_write_tokens")
    if cache_write is None:
        cache_write = _value(value, "cache_write_input_tokens")
    return TokenUsage(
        input_tokens=_optional_nonnegative_int(_value(value, "input_tokens")),
        cached_tokens=_optional_nonnegative_int(cached),
        cache_write_tokens=_optional_nonnegative_int(cache_write),
        output_tokens=_optional_nonnegative_int(_value(value, "output_tokens")),
        reasoning_tokens=_optional_nonnegative_int(
            _value(output_details, "reasoning_tokens")
        ),
        total_tokens=_optional_nonnegative_int(_value(value, "total_tokens")),
    )


def _render(value: object, limit: int, local_paths: Iterable[str | Path]) -> str:
    if isinstance(value, str):
        rendered = value
    else:
        try:
            rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            rendered = str(value)
    return sanitize_text(rendered, local_paths=local_paths)[:limit]


def _item_text(item: Mapping[str, Any]) -> str:
    for name in ("text", "message", "output", "content"):
        value = item.get(name)
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts = []
            for block in value:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, Mapping) and isinstance(block.get("text"), str):
                    parts.append(block["text"])
            if parts:
                return "\n".join(parts)
    return ""


def _tool_details(item: Mapping[str, Any]) -> tuple[str, object, object, bool]:
    item_type = str(item.get("type", ""))
    status = str(item.get("status", "")).lower()
    is_error = status in {"failed", "error", "cancelled"}
    if item_type == "command_execution":
        exit_code = _optional_nonnegative_int(item.get("exit_code"))
        return (
            "shell",
            item.get("command", ""),
            item.get("aggregated_output", item.get("output", "")),
            is_error or (exit_code is not None and exit_code != 0),
        )
    if item_type == "mcp_tool_call":
        tool = str(item.get("tool", item.get("name", "mcp_tool")))
        server = str(item.get("server", ""))
        return (
            f"{server}.{tool}".strip("."),
            item.get("arguments", item.get("input", {})),
            item.get("result", item.get("output", "")),
            is_error or bool(item.get("error")),
        )
    if item_type == "file_change":
        return (
            "apply_patch",
            item.get("changes", item.get("input", {})),
            item.get("output", status),
            is_error,
        )
    if item_type in {"web_search", "tool_call"}:
        return (
            str(item.get("name", item_type)),
            item.get("query", item.get("arguments", item.get("input", {}))),
            item.get("result", item.get("output", status)),
            is_error,
        )
    return "", {}, "", is_error


def _record_key(item: Mapping[str, Any]) -> str:
    identifier = item.get("id")
    if identifier is not None:
        return str(identifier)
    item_type = str(item.get("type", "unknown"))
    command = str(item.get("command", item.get("name", "")))
    return f"anonymous:{item_type}:{command}"


def adapt_codex_jsonl(
    lines: str | Iterable[str],
    *,
    goal: str = "",
    model: str = "",
    local_paths: Iterable[str | Path] = (),
    truncated: bool = False,
) -> CodexTrace:
    """Adapt JSONL text or lines into the package's normalized session model."""

    if isinstance(lines, str):
        source_lines = lines.splitlines()
    else:
        source_lines = lines
    safe_paths = tuple(local_paths)
    session = Session(
        path=Path("<codex-trace>"),
        source="codex",
        goal_override=sanitize_text(goal, local_paths=safe_paths),
        model="",
    )
    requested_model = sanitize_text(model, local_paths=safe_paths) or None
    usage = TokenUsage()
    turn_count = 0
    completed_tools = 0
    successful_terminal = False
    started_items: set[str] = set()
    completed_items: set[str] = set()

    for raw_line in source_lines:
        if not isinstance(raw_line, str) or not raw_line.strip():
            continue
        try:
            record = json.loads(raw_line)
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
        if not isinstance(record, Mapping):
            continue
        record_type = str(record.get("type", ""))
        if record_type in {"thread.started", "turn.started", "turn.completed"}:
            observed_model = record.get("model") or record.get("model_name")
            if isinstance(observed_model, str) and observed_model.strip():
                session.model = sanitize_text(observed_model, local_paths=safe_paths)

        if record_type == "turn.started":
            turn_count += 1
            continue
        if record_type == "turn.completed":
            successful_terminal = True
            session.terminal_subtype = "completed"
            session.terminal_is_error = False
            usage = usage + _usage_from(record.get("usage"))
            continue
        if record_type in {"turn.failed", "turn.cancelled", "error"}:
            successful_terminal = False
            session.terminal_subtype = record_type.rsplit(".", 1)[-1]
            session.terminal_is_error = True
            continue
        if record_type not in {"item.started", "item.completed"}:
            continue

        item = record.get("item")
        if not isinstance(item, Mapping):
            continue
        item_type = str(item.get("type", ""))
        if item_type == "reasoning":
            continue
        key = _record_key(item)

        if item_type in {"agent_message", "assistant_message"}:
            if record_type == "item.completed" and key not in completed_items:
                text = _item_text(item)
                if text.strip():
                    session.events.append(
                        Event(
                            kind="assistant",
                            text=sanitize_text(text, local_paths=safe_paths),
                        )
                    )
                completed_items.add(key)
            continue
        if item_type == "user_message":
            if record_type == "item.completed" and key not in completed_items:
                text = _item_text(item)
                if text.strip():
                    session.events.append(
                        Event(
                            kind="user",
                            text=sanitize_text(text, local_paths=safe_paths),
                        )
                    )
                completed_items.add(key)
            continue

        tool, arguments, result, is_error = _tool_details(item)
        if not tool:
            continue
        if key not in started_items:
            session.events.append(
                Event(
                    kind="tool_use",
                    tool=sanitize_text(tool, local_paths=safe_paths),
                    text=_render(arguments, 400, safe_paths),
                )
            )
            started_items.add(key)
        if record_type == "item.completed" and key not in completed_items:
            session.events.append(
                Event(
                    kind="tool_result",
                    text=_render(result, 2_000, safe_paths),
                    is_error=is_error,
                )
            )
            completed_items.add(key)
            completed_tools += 1

    return CodexTrace(
        session=session,
        requested_model=requested_model,
        usage=usage,
        turn_count=turn_count,
        completed_tool_count=completed_tools,
        successful_terminal=successful_terminal,
        truncated=truncated,
    )


def parse_codex_jsonl(
    path: str | Path,
    *,
    goal: str = "",
    model: str = "",
    max_bytes: int = 10_000_000,
    local_paths: Iterable[str | Path] = (),
) -> CodexTrace:
    """Read and adapt a bounded Codex JSONL file.

    I/O failures produce an ineligible empty trace, matching the tolerant
    behavior of the package's transcript adapters.
    """

    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    path = Path(path)
    try:
        with path.open("rb") as handle:
            raw = handle.read(max_bytes + 1)
    except OSError:
        raw = b""
    truncated = len(raw) > max_bytes
    text = raw[:max_bytes].decode("utf-8", errors="replace")
    return adapt_codex_jsonl(
        text,
        goal=goal,
        model=model,
        local_paths=(*local_paths, path.parent),
        truncated=truncated,
    )

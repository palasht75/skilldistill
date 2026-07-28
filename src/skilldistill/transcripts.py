"""Parse agent session transcripts into a normalized event stream.

Claude Code JSONL, Cursor Agent CLI stream-JSON, and exported Cursor Markdown
are supported. These formats are not stable public APIs, so parsing is
deliberately tolerant: unknown records are skipped and missing fields default
to empty.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

MAX_TRANSCRIPT_BYTES = 10_000_000
CURSOR_ROLE_HEADING = re.compile(
    r"^#{1,3}[ \t]+(?:\*\*)?(user|assistant|cursor)(?:\*\*)?[ \t]*$",
    re.IGNORECASE,
)
MARKDOWN_FENCE = re.compile(r"^[ \t]*(?P<marker>`{3,}|~{3,})")
SESSION_EXCLUDED_DIRS = {
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "node_modules",
    "venv",
}


@dataclass
class Event:
    kind: str  # "user" | "assistant" | "tool_use" | "tool_result"
    text: str = ""
    tool: str = ""
    is_error: bool = False
    call_id: str = ""


@dataclass
class Session:
    path: Path
    events: list[Event] = field(default_factory=list)
    source: str = "unknown"
    goal_override: str = ""
    session_id: str = ""
    model: str = ""
    duration_ms: int = 0
    terminal_subtype: str = ""
    terminal_is_error: bool = False

    @property
    def first_goal(self) -> str:
        if self.goal_override.strip():
            return self.goal_override.strip()
        for ev in self.events:
            if ev.kind == "user" and ev.text.strip():
                return ev.text.strip()
        return ""

    @property
    def tool_sequence(self) -> list[Event]:
        return [ev for ev in self.events if ev.kind == "tool_use"]

    @property
    def final_assistant_text(self) -> str:
        for ev in reversed(self.events):
            if ev.kind == "assistant" and ev.text.strip():
                return ev.text.strip()
        return ""


def _blocks(content) -> list:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    return []


def _json_preview(value, limit: int) -> str:
    try:
        rendered = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        rendered = str(value)
    return rendered[:limit]


def _cursor_tool_payload(tool_call) -> tuple[str, dict]:
    if not isinstance(tool_call, dict):
        return "", {}
    for variant, payload in tool_call.items():
        if not isinstance(payload, dict):
            continue
        if variant.endswith("ToolCall"):
            return variant[: -len("ToolCall")], payload
    return "", tool_call


def _append_message_blocks(session: Session, kind, message) -> None:
    if not isinstance(message, dict):
        return
    for block in _blocks(message.get("content")):
        block_type = block.get("type")
        if block_type == "text" and kind in ("user", "assistant"):
            session.events.append(Event(kind=kind, text=str(block.get("text", ""))))
        elif block_type == "tool_use":
            name = str(block.get("name", ""))
            brief = _json_preview(block.get("input", {}), 200)
            session.events.append(
                Event(
                    kind="tool_use",
                    tool=name,
                    text=brief,
                    call_id=str(block.get("id", "")),
                )
            )
        elif block_type == "tool_result":
            content = block.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    str(item.get("text", "")) for item in content if isinstance(item, dict)
                )
            session.events.append(
                Event(
                    kind="tool_result",
                    text=str(content)[:500],
                    is_error=bool(block.get("is_error", False)),
                    call_id=str(block.get("tool_use_id", "")),
                )
            )


def _parse_jsonl(path: Path, max_bytes: int) -> Session:
    session = Session(path=path)
    seen_tool_calls: set[str] = set()
    try:
        handle = path.open("rb")
    except OSError:
        return session
    with handle:
        remaining = max_bytes
        while remaining > 0:
            raw_line = handle.readline(remaining + 1)
            if not raw_line:
                break
            if len(raw_line) > remaining:
                break
            remaining -= len(raw_line)
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(obj, dict):
                continue

            kind = obj.get("type")
            if not isinstance(kind, str):
                kind = ""
            if obj.get("session_id"):
                session.session_id = str(obj["session_id"])
            if obj.get("model"):
                session.model = str(obj["model"])
            message = obj.get("message")
            _append_message_blocks(session, kind, message)

            if kind == "tool_call":
                session.source = "cursor"
                call_id = str(obj.get("call_id", ""))
                tool_name, payload = _cursor_tool_payload(obj.get("tool_call"))
                subtype = str(obj.get("subtype", ""))
                if subtype == "started" or call_id not in seen_tool_calls:
                    session.events.append(
                        Event(
                            kind="tool_use",
                            tool=tool_name or "tool",
                            text=_json_preview(payload.get("args", {}), 200),
                            call_id=call_id,
                        )
                    )
                    seen_tool_calls.add(call_id)
                if subtype != "started":
                    result = payload.get("result", obj.get("result", {}))
                    is_error = bool(obj.get("is_error", False)) or subtype in {
                        "failed",
                        "error",
                    }
                    if isinstance(result, dict):
                        is_error = is_error or "failure" in result or "error" in result
                    session.events.append(
                        Event(
                            kind="tool_result",
                            text=_json_preview(result, 500),
                            is_error=is_error,
                            call_id=call_id,
                        )
                    )
            elif kind == "result":
                session.source = "cursor"
                terminal_subtype = str(obj.get("subtype", "")).lower()
                session.terminal_subtype = terminal_subtype
                session.terminal_is_error = bool(obj.get("is_error", False)) or (
                    terminal_subtype in {"error", "failed", "failure"}
                )
                duration = obj.get("duration_ms")
                if isinstance(duration, (int, float)):
                    session.duration_ms = int(duration)
                result_text = obj.get("result")
                if (
                    isinstance(result_text, str)
                    and result_text.strip()
                    and not any(
                        event.kind == "assistant" and event.text == result_text
                        for event in session.events[-3:]
                    )
                ):
                    session.events.append(Event(kind="assistant", text=result_text))
            elif kind in {"user", "assistant"} and session.source == "unknown":
                session.source = "claude"
    return session


def _parse_markdown(path: Path, max_bytes: int) -> Session:
    session = Session(path=path, source="cursor")
    try:
        with path.open("rb") as handle:
            text = handle.read(max_bytes).decode("utf-8", errors="replace")
    except OSError:
        return session

    role = ""
    buffer: list[str] = []

    def flush() -> None:
        if not role:
            return
        content = "\n".join(buffer).strip()
        if content:
            event_kind = "assistant" if role in {"assistant", "cursor"} else "user"
            session.events.append(Event(kind=event_kind, text=content))

    lines = text.splitlines()
    fence_character = ""
    fence_length = 0
    for index, line in enumerate(lines):
        fence_match = MARKDOWN_FENCE.match(line)
        if fence_match:
            marker = fence_match.group("marker")
            if not fence_character:
                fence_character = marker[0]
                fence_length = len(marker)
            elif marker[0] == fence_character and len(marker) >= fence_length:
                fence_character = ""
                fence_length = 0
            if role:
                buffer.append(line)
            continue

        if not fence_character:
            match = CURSOR_ROLE_HEADING.match(line)
            if match:
                flush()
                role = match.group(1).lower()
                buffer = []
                continue
            if role and line.strip() == "---":
                next_nonblank = next(
                    (
                        candidate
                        for candidate in lines[index + 1 :]
                        if candidate.strip()
                    ),
                    "",
                )
                if CURSOR_ROLE_HEADING.match(next_nonblank):
                    continue
        if role:
            buffer.append(line)
    flush()
    return session


def parse_session(
    path: Path | str,
    *,
    max_bytes: int = MAX_TRANSCRIPT_BYTES,
) -> Session:
    """Parse Claude JSONL, Cursor CLI stream-JSON, or exported Cursor Markdown."""

    path = Path(path)
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    if path.suffix.lower() in {".md", ".markdown"}:
        return _parse_markdown(path, max_bytes)
    return _parse_jsonl(path, max_bytes)


def _markdown_roles(text: str) -> set[str]:
    roles = set()
    fence_character = ""
    fence_length = 0
    for line in text.splitlines():
        fence_match = MARKDOWN_FENCE.match(line)
        if fence_match:
            marker = fence_match.group("marker")
            if not fence_character:
                fence_character = marker[0]
                fence_length = len(marker)
            elif marker[0] == fence_character and len(marker) >= fence_length:
                fence_character = ""
                fence_length = 0
            continue
        if not fence_character and (match := CURSOR_ROLE_HEADING.match(line)):
            roles.add(match.group(1).lower())
    return roles


def _looks_like_cursor_markdown(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            prefix = handle.read(32_000)
    except OSError:
        return False
    roles = _markdown_roles(prefix)
    return "user" in roles and bool({"assistant", "cursor"} & roles)


def find_sessions(root: Path | str, *, source: str = "auto") -> list[Path]:
    """Find supported session files under ``root``, newest first."""

    root = Path(root).expanduser()
    if source not in {"auto", "claude", "cursor"}:
        raise ValueError("source must be auto, claude, or cursor")
    files = []
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [
            name for name in dirnames if name not in SESSION_EXCLUDED_DIRS
        ]
        parent = Path(directory)
        for filename in filenames:
            path = parent / filename
            suffix = path.suffix.lower()
            if suffix == ".jsonl" or (
                source in {"auto", "cursor"}
                and suffix in {".md", ".markdown"}
                and _looks_like_cursor_markdown(path)
            ):
                files.append(path)

    def modified(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    return sorted(set(files), key=modified, reverse=True)

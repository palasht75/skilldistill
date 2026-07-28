"""Parse agent session transcripts into a normalized event stream.

v0.1 supports Claude Code session files: JSONL, one event per line, found in
``~/.claude/projects/<project-slug>/<session-id>.jsonl``. The format is not a
stable public API, so parsing is deliberately tolerant: unknown lines are
skipped, missing fields default to empty.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Event:
    kind: str  # "user" | "assistant" | "tool_use" | "tool_result"
    text: str = ""
    tool: str = ""
    is_error: bool = False


@dataclass
class Session:
    path: Path
    events: list[Event] = field(default_factory=list)

    @property
    def first_goal(self) -> str:
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


def parse_session(path: Path | str) -> Session:
    path = Path(path)
    session = Session(path=path)
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        kind = obj.get("type")
        message = obj.get("message") if isinstance(obj.get("message"), dict) else {}
        for block in _blocks(message.get("content")):
            btype = block.get("type")
            if btype == "text" and kind in ("user", "assistant"):
                session.events.append(Event(kind=kind, text=str(block.get("text", ""))))
            elif btype == "tool_use":
                name = str(block.get("name", ""))
                brief = json.dumps(block.get("input", {}))[:200]
                session.events.append(Event(kind="tool_use", tool=name, text=brief))
            elif btype == "tool_result":
                content = block.get("content", "")
                if isinstance(content, list):
                    content = " ".join(
                        str(c.get("text", "")) for c in content if isinstance(c, dict)
                    )
                session.events.append(
                    Event(
                        kind="tool_result",
                        text=str(content)[:500],
                        is_error=bool(block.get("is_error", False)),
                    )
                )
    return session


def find_sessions(root: Path | str) -> list[Path]:
    """All .jsonl session files under ``root``, newest first."""
    root = Path(root).expanduser()
    files = sorted(root.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files

"""Resolve an LLM callable from installed SDKs + env keys. Never required:
everything works offline with the template fallback."""

from __future__ import annotations

import os
from typing import Callable

LLMFn = Callable[[str], str]

ANTHROPIC_MODEL = os.environ.get("SKILLDISTILL_ANTHROPIC_MODEL", "claude-sonnet-4-5")
OPENAI_MODEL = os.environ.get("SKILLDISTILL_OPENAI_MODEL", "gpt-5.2")


def resolve_llm() -> LLMFn | None:
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic

            client = anthropic.Anthropic()

            def call(prompt: str) -> str:
                msg = client.messages.create(
                    model=ANTHROPIC_MODEL,
                    max_tokens=2048,
                    messages=[{"role": "user", "content": prompt}],
                )
                return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")

            return call
        except ImportError:
            pass
    if os.environ.get("OPENAI_API_KEY"):
        try:
            import openai

            client = openai.OpenAI()

            def call(prompt: str) -> str:
                resp = client.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                )
                return resp.choices[0].message.content or ""

            return call
        except ImportError:
            pass
    return None

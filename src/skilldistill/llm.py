"""Resolve an LLM callable from installed SDKs + env keys. Never required:
everything works offline with the template fallback."""

from __future__ import annotations

import os
from typing import Callable

LLMFn = Callable[[str], str]

ANTHROPIC_MODEL = os.environ.get("SKILLDISTILL_ANTHROPIC_MODEL", "claude-sonnet-4-5")
OPENAI_MODEL = os.environ.get("SKILLDISTILL_OPENAI_MODEL", "gpt-5.6-luna")


class LLMProviderError(RuntimeError):
    """A sanitized provider error safe to show in the CLI."""


def _anthropic_llm() -> LLMFn | None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
    except ImportError:
        return None

    client = anthropic.Anthropic(timeout=60.0, max_retries=2)

    def call(prompt: str) -> str:
        try:
            msg = client.messages.create(
                model=ANTHROPIC_MODEL,
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:
            raise LLMProviderError(
                f"Anthropic request failed ({type(exc).__name__})"
            ) from exc
        return "".join(block.text for block in msg.content if getattr(block, "type", "") == "text")

    return call


def _openai_llm() -> LLMFn | None:
    if not os.environ.get("OPENAI_API_KEY"):
        return None
    try:
        import openai
    except ImportError:
        return None

    client = openai.OpenAI(timeout=60.0, max_retries=2)

    def call(prompt: str) -> str:
        try:
            response = client.responses.create(
                model=OPENAI_MODEL,
                input=prompt,
                max_output_tokens=2048,
            )
        except Exception as exc:
            raise LLMProviderError(f"OpenAI request failed ({type(exc).__name__})") from exc
        return response.output_text or ""

    return call


def resolve_llm(provider: str = "auto") -> LLMFn | None:
    """Resolve an explicitly selected provider.

    ``auto`` preserves the Python API's historical behavior. The CLI uses an
    offline default so ambient API keys never imply transcript egress.
    """

    if provider == "offline":
        return None
    if provider == "anthropic":
        return _anthropic_llm()
    if provider == "openai":
        return _openai_llm()
    if provider == "auto":
        return _anthropic_llm() or _openai_llm()
    raise ValueError("provider must be offline, openai, anthropic, or auto")

import sys
from types import SimpleNamespace

import pytest

from skilldistill import llm


def test_offline_provider_never_uses_ambient_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-key")

    assert llm.resolve_llm(provider="offline") is None


def test_openai_provider_uses_responses_api(monkeypatch):
    observed = {}

    class Responses:
        def create(self, **kwargs):
            observed.update(kwargs)
            return SimpleNamespace(output_text="generated skill")

    class Client:
        def __init__(self, **kwargs):
            observed["client"] = kwargs
            self.responses = Responses()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(llm, "OPENAI_MODEL", "test-model")
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=Client))

    call = llm.resolve_llm(provider="openai")

    assert call is not None
    assert call("prompt") == "generated skill"
    assert observed["model"] == "test-model"
    assert observed["input"] == "prompt"
    assert observed["max_output_tokens"] == 2048
    assert observed["client"] == {"timeout": 60.0, "max_retries": 2}


def test_provider_errors_are_sanitized(monkeypatch):
    class Responses:
        def create(self, **_kwargs):
            raise RuntimeError("request failed with secret test-key")

    class Client:
        def __init__(self, **_kwargs):
            self.responses = Responses()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=Client))
    call = llm.resolve_llm(provider="openai")

    with pytest.raises(llm.LLMProviderError) as error:
        call("prompt")

    assert "test-key" not in str(error.value)
    assert "RuntimeError" in str(error.value)


def test_explicit_provider_requires_matching_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    assert llm.resolve_llm(provider="openai") is None


def test_rejects_unknown_provider():
    with pytest.raises(ValueError, match="provider"):
        llm.resolve_llm(provider="mystery")

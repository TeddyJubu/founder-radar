"""The LLM provider seam: OpenAILLM + the env-driven build_llm() factory.

`AnthropicLLM` is exercised through the extraction golden tests; this file
covers the second provider and the factory that picks between them. The
transport is injected, so nothing here touches a socket (conftest blocks all
non-loopback traffic in the offline suite).
"""

from __future__ import annotations

import json

import pytest

from radar.extract.llm import (
    AnthropicLLM,
    InvalidModelJSON,
    OpenAILLM,
    ProviderDown,
    build_llm,
)


# ------------------------------------------------------------------ fakes


class FakeResponse:
    def __init__(self, payload, *, status=200):
        self._payload = payload
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise ProviderDown(f"HTTP {self.status}")

    def json(self):
        return self._payload


class FakeClient:
    """Stand-in for httpx.Client. Records the last call for assertions."""

    def __init__(self, responses=None):
        self._responses = list(responses or [])
        self.calls = []

    def post(self, url, *, headers=None, json=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        if self._responses:
            return self._responses.pop(0)
        raise RuntimeError("unexpected request — no canned response left")

    def _reset(self):
        self.calls = []


def _completion(content: str, *, tokens_in=27, tokens_out=63, model="kilo-auto/free") -> dict:
    return {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": tokens_in, "completion_tokens": tokens_out},
        "model": model,
    }


# ------------------------------------------------------------- OpenAILLM


def test_openai_completion_parses_payload_and_usage():
    client = FakeClient([FakeResponse(_completion('{"ok": true}'))])
    llm = OpenAILLM("kilo-auto/free", api_key="k", base_url="https://gw/v1", client=client)

    response = llm.complete(key="k1", system="sys", user="usr")

    assert response.payload == {"ok": True}
    assert response.tokens_in == 27
    assert response.tokens_out == 63
    assert response.cost_usd == 0.0  # unknown model id — the free tier records £0 honestly
    call = client.calls[0]
    assert call["url"] == "https://gw/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer k"
    body = call["json"]
    assert body["model"] == "kilo-auto/free"
    assert body["temperature"] == 0
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][0]["content"].startswith("sys")
    assert "company_name" in body["messages"][0]["content"]  # the schema hint
    assert body["messages"][1]["content"] == "usr"


def test_openai_strips_markdown_fences():
    client = FakeClient([FakeResponse(_completion('```json\n{"ok": true}\n```'))])
    llm = OpenAILLM("m", api_key="k", base_url="https://gw/v1", client=client)
    assert llm.complete(key="k1", system="s", user="u").payload == {"ok": True}


def test_openai_empty_content_retries_once_then_provider_down():
    # A reasoning model can spend the whole budget thinking and return
    # content: null — a bad draw, not a dead provider. One retry happens
    # inside the client; two empty draws degrade to ProviderDown (→ heuristic),
    # never to a quarantine of bad JSON.
    client = FakeClient([FakeResponse(_completion(None)), FakeResponse(_completion(None))])
    llm = OpenAILLM("m", api_key="k", base_url="https://gw/v1", client=client)
    with pytest.raises(ProviderDown):
        llm.complete(key="k1", system="s", user="u")
    assert len(client.calls) == 2


def test_openai_empty_content_then_success_recovers():
    client = FakeClient([FakeResponse(_completion(None)), FakeResponse(_completion('{"ok": true}'))])
    llm = OpenAILLM("m", api_key="k", base_url="https://gw/v1", client=client)
    assert llm.complete(key="k1", system="s", user="u").payload == {"ok": True}
    assert len(client.calls) == 2


def test_openai_non_json_content_is_invalid_model_json():
    client = FakeClient([FakeResponse(_completion("definitely not json"))])
    llm = OpenAILLM("m", api_key="k", base_url="https://gw/v1", client=client)
    with pytest.raises(InvalidModelJSON):
        llm.complete(key="k1", system="s", user="u")


def test_openai_transport_error_is_provider_down():
    llm = OpenAILLM("m", api_key="k", base_url="https://gw/v1", client=FakeClient([]))
    with pytest.raises(ProviderDown):
        llm.complete(key="k1", system="s", user="u")


def test_openai_http_error_is_provider_down():
    client = FakeClient([FakeResponse({"error": "nope"}, status=402)])
    llm = OpenAILLM("m", api_key="k", base_url="https://gw/v1", client=client)
    with pytest.raises(ProviderDown):
        llm.complete(key="k1", system="s", user="u")


# -------------------------------------------------------------- build_llm


def test_build_llm_no_key_is_none(monkeypatch):
    for name in ("LLM_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "LLM_PROVIDER", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    assert build_llm() is None


def test_build_llm_defaults_to_anthropic(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-ant-x")
    llm = build_llm()
    assert isinstance(llm, AnthropicLLM)
    assert llm.model_id == "claude-haiku-4-5-20251001"


def test_build_llm_anthropic_prefers_provider_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-y")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    llm = build_llm()
    assert isinstance(llm, AnthropicLLM)
    assert llm._api_key == "sk-ant-y"


def test_build_llm_openai_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_API_KEY", "kilo-key")
    monkeypatch.setenv("LLM_MODEL", "kilo-auto/free")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.kilo.ai/api/gateway/v1")
    llm = build_llm()
    assert isinstance(llm, OpenAILLM)
    assert llm.model_id == "kilo-auto/free"
    assert llm._base_url == "https://api.kilo.ai/api/gateway/v1"


def test_build_llm_openai_without_key_is_none(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    for name in ("LLM_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert build_llm() is None


def test_openai_base_url_trailing_slash_normalised():
    client = FakeClient([FakeResponse(_completion('{"ok": true}'))])
    llm = OpenAILLM("m", api_key="k", base_url="https://gw/v1/", client=client)
    llm.complete(key="k1", system="s", user="u")
    assert client.calls[0]["url"] == "https://gw/v1/chat/completions"


def test_openai_payload_json_dumps_roundtrip():
    """The payload must survive the llm_cache's json.dumps round-trip."""
    client = FakeClient([FakeResponse(_completion(json.dumps({"ok": True})))])
    llm = OpenAILLM("m", api_key="k", base_url="https://gw/v1", client=client)
    payload = llm.complete(key="k1", system="s", user="u").payload
    assert json.loads(json.dumps(payload)) == {"ok": True}

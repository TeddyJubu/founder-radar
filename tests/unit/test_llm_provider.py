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
        self.status_code = status

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


def test_openai_retries_without_json_mode_when_the_gateway_rejects_it():
    # Oct 2026: `kilo-auto/free` routed to a backend that answers 400 to
    # `response_format`, and every article fell back to the heuristic.
    client = FakeClient([
        FakeResponse({"error": "upstream_error"}, status=400),
        FakeResponse(_completion('{"ok": true}')),
        FakeResponse(_completion('{"ok": 2}')),
    ])
    llm = OpenAILLM("kilo-auto/free", api_key="k", base_url="https://gw/v1", client=client)

    assert llm.complete(key="k1", system="s", user="u").payload == {"ok": True}
    assert "response_format" in client.calls[0]["json"]
    assert "response_format" not in client.calls[1]["json"]
    # Learned once per client: the next article does not pay the 400 again.
    assert llm.complete(key="k2", system="s", user="u").payload == {"ok": 2}
    assert len(client.calls) == 3
    assert "response_format" not in client.calls[2]["json"]


def test_openai_400_without_json_mode_is_still_provider_down():
    client = FakeClient([
        FakeResponse({"error": "bad"}, status=400),
        FakeResponse({"error": "bad"}, status=400),
    ])
    llm = OpenAILLM("m", api_key="k", base_url="https://gw/v1", client=client)
    with pytest.raises(ProviderDown):
        llm.complete(key="k1", system="s", user="u")
    assert len(client.calls) == 2


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


# ------------------------------------------------------------- HermesLLM


class _Done:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


def _hermes(runner, monkeypatch):
    from radar.extract.llm import HermesLLM
    monkeypatch.setattr("radar.qa.publish._ensure_hermes_acl", lambda: None)
    return HermesLLM(binary="/usr/bin/hermes", runner=runner)


def test_hermes_extraction_is_one_tool_free_turn_and_parses_the_json(monkeypatch):
    seen = {}

    def runner(argv, **kw):
        seen.update(argv=argv, input=kw["input"], env=kw["env"])
        return _Done('Sure.\n```json\n{"company_name": "Kelvin Bio"}\n```')

    response = _hermes(runner, monkeypatch).complete(key="k", system="SYS", user="ARTICLE")
    assert response.payload == {"company_name": "Kelvin Bio"}
    argv = seen["argv"]
    assert argv[1:3] == ["chat", "-Q"] and "--oneshot" in argv
    assert argv[argv.index("--max-turns") + 1] == "1" and "--ignore-rules" in argv
    assert seen["input"].startswith("SYS") and seen["input"].endswith("ARTICLE")
    assert "company_name" in seen["input"]            # the schema travels with it
    assert seen["env"]["HERMES_DISABLE_LAZY_INSTALLS"] == "1"


@pytest.mark.parametrize("outcome", ["exit", "timeout", "empty"])
def test_hermes_that_cannot_answer_is_provider_down(monkeypatch, outcome):
    import subprocess

    def runner(argv, **kw):
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(argv, kw["timeout"])
        return _Done("", 1 if outcome == "exit" else 0, "boom")

    with pytest.raises(ProviderDown):
        _hermes(runner, monkeypatch).complete(key="k", system="s", user="u")


def test_hermes_prose_without_json_is_invalid_model_json(monkeypatch):
    with pytest.raises(InvalidModelJSON):
        _hermes(lambda argv, **kw: _Done("I could not find a company."), monkeypatch
                ).complete(key="k", system="s", user="u")


class _Scripted:
    def __init__(self, model_id, outcomes):
        self.model_id, self.outcomes, self.calls = model_id, list(outcomes), 0

    def complete(self, *, key, system, user):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        from radar.extract.llm import LLMResponse
        return LLMResponse(payload=outcome, model_id=self.model_id)


def test_fallback_uses_the_secondary_only_when_the_primary_is_down():
    from radar.extract.llm import FallbackLLM
    primary = _Scripted("hermes", [{"a": 1}, ProviderDown("x"), InvalidModelJSON("bad")])
    secondary = _Scripted("kilo", [{"b": 2}])
    llm = FallbackLLM(primary, secondary)
    assert llm.complete(key="1", system="s", user="u").payload == {"a": 1}
    assert llm.complete(key="2", system="s", user="u").payload == {"b": 2}
    with pytest.raises(InvalidModelJSON):          # bad JSON is not an outage
        llm.complete(key="3", system="s", user="u")
    assert secondary.calls == 1


def test_fallback_stops_asking_a_dead_primary_after_three_failures():
    from radar.extract.llm import FallbackLLM
    primary = _Scripted("hermes", [ProviderDown("x")] * 3)
    secondary = _Scripted("kilo", [{"n": i} for i in range(5)])
    llm = FallbackLLM(primary, secondary)
    for _ in range(5):
        llm.complete(key="k", system="s", user="u")
    assert primary.calls == 3 and secondary.calls == 5


def test_build_llm_hermes_falls_back_to_the_configured_gateway(monkeypatch):
    from radar.extract.llm import FallbackLLM, HermesLLM
    monkeypatch.setenv("LLM_PROVIDER", "hermes")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_MODEL", "kilo-auto/free")
    monkeypatch.setenv("LLM_BASE_URL", "https://gw/v1")
    llm = build_llm()
    assert isinstance(llm, FallbackLLM)
    assert isinstance(llm.primary, HermesLLM) and isinstance(llm.secondary, OpenAILLM)
    assert llm.secondary.model_id == "kilo-auto/free"
    for name in ("LLM_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert isinstance(build_llm(), HermesLLM)


def test_settings_no_longer_default_to_a_claude_model():
    from radar.config.models import Settings
    assert "claude" not in Settings().llm_model

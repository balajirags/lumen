from __future__ import annotations

import time

import pytest

from codedoc import llm
from codedoc.llm import (
    CopilotProvider,
    create_provider,
    resolve_github_oauth_token,
    resolve_provider_name,
    uses_anthropic_format,
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    for var in ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(llm, "_read_copilot_config_tokens", lambda: [])
    monkeypatch.setattr(llm, "_read_gh_cli_token", lambda: None)
    CopilotProvider._cache.clear()


def _fake_exchange(calls: list, token: str = "tid=1", ttl: int = 1800, api: str | None = None):
    def exchange(self):
        calls.append(1)
        data = {"token": f"{token}-{len(calls)}", "expires_at": time.time() + ttl}
        if api:
            data["endpoints"] = {"api": api}
        return data

    return exchange


def test_token_resolution_order(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "from-github")
    monkeypatch.setenv("GH_TOKEN", "from-gh")
    assert resolve_github_oauth_token() == "from-gh"
    monkeypatch.setenv("COPILOT_GITHUB_TOKEN", "from-copilot")
    assert resolve_github_oauth_token() == "from-copilot"
    assert resolve_github_oauth_token("explicit") == "explicit"


def test_stale_token_skipped_during_exchange(monkeypatch):
    import httpx

    seen = []

    def fake_get(url, headers=None, timeout=None):
        tok = headers["Authorization"].split()[1]
        seen.append(tok)
        if tok == "stale":
            return httpx.Response(401, json={"message": "Bad credentials"})
        return httpx.Response(200, json={"token": "tid=ok", "expires_at": time.time() + 1800})

    monkeypatch.setattr(llm, "_read_copilot_config_tokens", lambda: ["stale", "good"])
    monkeypatch.setattr(httpx, "get", fake_get)
    provider = CopilotProvider("gpt-4.1")
    assert provider.client.api_key == "tid=ok"
    assert seen == ["stale", "good"]
    assert provider._oauth_tokens[0] == "good"


def test_missing_token_raises():
    with pytest.raises(RuntimeError, match="No GitHub token"):
        resolve_github_oauth_token()


def test_provider_resolution():
    assert resolve_provider_name("copilot", "gpt-4.1") == "copilot"
    assert resolve_provider_name("auto", "claude-sonnet-4-6", "https://api.githubcopilot.com") == "copilot"
    assert resolve_provider_name("auto", "claude-sonnet-4-6") == "claude"
    assert resolve_provider_name("anthropic", "x") == "claude"
    # Claude models served via Copilot use the OpenAI message format
    assert not uses_anthropic_format("copilot", "claude-sonnet-4.5")
    assert not uses_anthropic_format("auto", "claude-sonnet-4.5", "https://api.githubcopilot.com")
    assert uses_anthropic_format("anthropic", "claude-sonnet-4-6")


def test_create_provider_copilot_uses_token_and_endpoint(monkeypatch):
    calls: list = []
    monkeypatch.setattr(CopilotProvider, "_exchange_token", _fake_exchange(calls, api="https://api.business.githubcopilot.com"))
    provider = create_provider("copilot", "gpt-4.1", api_key="gho_abc")
    assert isinstance(provider, CopilotProvider)
    assert provider.client.api_key == "tid=1-1"
    assert str(provider.client.base_url).startswith("https://api.business.githubcopilot.com")
    assert provider.client.default_headers["Copilot-Integration-Id"] == "vscode-chat"


def test_token_cached_then_refreshed_near_expiry(monkeypatch):
    calls: list = []
    monkeypatch.setattr(CopilotProvider, "_exchange_token", _fake_exchange(calls))
    a = CopilotProvider("gpt-4.1", api_key="gho_abc")
    b = CopilotProvider("gpt-4.1", api_key="gho_abc")
    assert len(calls) == 1  # shared cache across instances
    CopilotProvider._cache["gho_abc"]["expires_at"] = time.time() + 10
    a._refresh_client()
    assert len(calls) == 2
    assert a.client.api_key == "tid=1-2"
    b._refresh_client()
    assert b.client.api_key == "tid=1-2"


def test_chat_retries_once_on_401(monkeypatch):
    calls: list = []
    monkeypatch.setattr(CopilotProvider, "_exchange_token", _fake_exchange(calls))
    provider = CopilotProvider("gpt-4.1", api_key="gho_abc")

    class Unauthorized(Exception):
        status_code = 401

    attempts = []

    def fake_chat(self, messages, tools=None, tool_choice="auto"):
        attempts.append(self.client.api_key)
        if len(attempts) == 1:
            raise Unauthorized()
        return "ok"

    monkeypatch.setattr(llm.OpenAIProvider, "chat", fake_chat)
    assert provider.chat([{"role": "user", "content": "hi"}]) == "ok"
    assert attempts == ["tid=1-1", "tid=1-2"]

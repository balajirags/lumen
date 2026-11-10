"""LLM provider abstraction layer.

Supports Claude (native Anthropic SDK), Ollama, OpenAI and GitHub Copilot (OpenAI-compatible APIs).
The provider is injected into the agent — never hardcoded.

Usage::

    from codedoc.llm import create_provider

    provider = create_provider("claude", "claude-sonnet-4-6")
    response = provider.chat(messages, tools=tool_defs)

    provider = create_provider("ollama", "qwen3.5:35b", base_url="http://127.0.0.1:11434/v1")
    response = provider.chat(messages, tools=tool_defs)
"""

from __future__ import annotations

import json
import os
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Tool definition helpers
# ---------------------------------------------------------------------------

@dataclass
class ToolParam:
    """A single parameter in a tool definition."""
    name: str
    type: str
    description: str
    required: bool = True


@dataclass
class ToolDefinition:
    """A portable tool definition that can emit both OpenAI and Anthropic formats."""
    name: str
    description: str
    params: list[ToolParam] = field(default_factory=list)

    def to_openai_dict(self) -> dict:
        properties = {p.name: {"type": p.type, "description": p.description} for p in self.params}
        required = [p.name for p in self.params if p.required]
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }

    def to_anthropic_dict(self) -> dict:
        properties = {p.name: {"type": p.type, "description": p.description} for p in self.params}
        required = [p.name for p in self.params if p.required]
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        }


# ---------------------------------------------------------------------------
# Normalized response types
# ---------------------------------------------------------------------------

@dataclass
class ToolCall:
    """A single tool invocation requested by the LLM."""
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    """Normalized response from any LLM provider."""
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "stop"  # "stop", "tool_use", "max_tokens", "error"
    input_tokens: int = 0
    output_tokens: int = 0


# ---------------------------------------------------------------------------
# Provider protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class LLMProvider(Protocol):
    """Protocol that all LLM providers must satisfy."""

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
    ) -> LLMResponse: ...


# ---------------------------------------------------------------------------
# Ollama provider (OpenAI-compatible API)
# ---------------------------------------------------------------------------

class OllamaProvider:
    """Wraps the OpenAI client pointed at an Ollama (or compatible) endpoint."""

    def __init__(
        self,
        model: str,
        base_url: str = "http://127.0.0.1:11434/v1",
        num_ctx: int = 131072,
    ):
        from openai import OpenAI
        self.model = model
        self.base_url = base_url
        self.num_ctx = num_ctx
        self.client = OpenAI(base_url=base_url, api_key="ollama")

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            # Pass num_ctx so Ollama allocates the full context window per request.
            # Without this the Ollama app uses its default (often 4096-8192) regardless
            # of the model's capability. extra_body is forwarded as-is to the Ollama API.
            "extra_body": {"options": {"num_ctx": self.num_ctx}},
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice

        response = self.client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        msg = choice.message

        tool_calls: list[ToolCall] = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    args = {}
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=args,
                ))

        stop = "tool_use" if tool_calls else "stop"
        usage = getattr(response, "usage", None)
        return LLMResponse(
            content=msg.content,
            tool_calls=tool_calls,
            stop_reason=stop,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )


# ---------------------------------------------------------------------------
# Claude provider (native Anthropic SDK)
# ---------------------------------------------------------------------------

class ClaudeProvider:
    """Wraps the native Anthropic Messages API."""

    def __init__(self, model: str, api_key: str | None = None, max_tokens: int = 16384):
        import anthropic
        self.model = model
        self.max_tokens = max_tokens
        self.client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
    ) -> LLMResponse:
        # Separate system message from conversation messages
        system_text = ""
        conversation: list[dict[str, Any]] = []
        for m in messages:
            if m["role"] == "system":
                system_text = m["content"]
            elif m["role"] == "tool":
                # Convert OpenAI tool result format to Anthropic format
                conversation.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": m.get("tool_call_id", ""),
                        "content": m.get("content", ""),
                    }],
                })
            elif m["role"] == "assistant" and m.get("tool_calls"):
                # Convert OpenAI assistant tool_calls to Anthropic content blocks
                content_blocks: list[dict] = []
                if m.get("content"):
                    content_blocks.append({"type": "text", "text": m["content"]})
                for tc in m["tool_calls"]:
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc["id"] if isinstance(tc, dict) else tc.id,
                        "name": tc["name"] if isinstance(tc, dict) else tc.name,
                        "input": tc["arguments"] if isinstance(tc, dict) else tc.arguments,
                    })
                conversation.append({"role": "assistant", "content": content_blocks})
            else:
                conversation.append(m)

        # Merge consecutive same-role messages (Anthropic requires alternating)
        conversation = self._merge_consecutive(conversation)

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": conversation,
        }
        if system_text:
            kwargs["system"] = system_text
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = {"type": tool_choice}

        response = self.client.messages.create(**kwargs)

        content_text = ""
        tool_calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                content_text += block.text
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    arguments=block.input if isinstance(block.input, dict) else {},
                ))

        stop = "tool_use" if tool_calls else "stop"
        if response.stop_reason == "max_tokens":
            stop = "max_tokens"

        usage = getattr(response, "usage", None)
        return LLMResponse(
            content=content_text or None,
            tool_calls=tool_calls,
            stop_reason=stop,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
        )

    @staticmethod
    def _merge_consecutive(messages: list[dict]) -> list[dict]:
        """Merge consecutive messages with the same role."""
        if not messages:
            return messages
        merged: list[dict] = [messages[0]]
        for m in messages[1:]:
            if m["role"] == merged[-1]["role"]:
                # Merge content
                prev = merged[-1]
                prev_content = prev.get("content", "")
                new_content = m.get("content", "")
                if isinstance(prev_content, str) and isinstance(new_content, str):
                    prev["content"] = prev_content + "\n" + new_content
                elif isinstance(prev_content, list) and isinstance(new_content, list):
                    prev["content"] = prev_content + new_content
                elif isinstance(prev_content, str) and isinstance(new_content, list):
                    prev["content"] = [{"type": "text", "text": prev_content}] + new_content
                elif isinstance(prev_content, list) and isinstance(new_content, str):
                    prev["content"] = prev_content + [{"type": "text", "text": new_content}]
            else:
                merged.append(m)
        return merged


# ---------------------------------------------------------------------------
# OpenAI-compatible provider (for OpenAI API, Azure OpenAI, etc.)
# ---------------------------------------------------------------------------

class OpenAIProvider:
    """Wraps any OpenAI-compatible API (OpenAI, Azure, etc.)."""

    def __init__(self, model: str, api_key: str | None = None, base_url: str | None = None):
        from openai import OpenAI
        self.model = model
        kwargs: dict[str, Any] = {}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        self.client = OpenAI(**kwargs)

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice

        response = self.client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        msg = choice.message

        tool_calls: list[ToolCall] = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    args = {}
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=args,
                ))

        stop = "tool_use" if tool_calls else "stop"
        usage = getattr(response, "usage", None)
        return LLMResponse(
            content=msg.content,
            tool_calls=tool_calls,
            stop_reason=stop,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )


# ---------------------------------------------------------------------------
# GitHub Copilot provider (OpenAI-compatible chat API with token exchange)
# ---------------------------------------------------------------------------

_COPILOT_TOKEN_URL = "https://api.github.com/copilot_internal/v2/token"
_COPILOT_DEFAULT_BASE_URL = "https://api.githubcopilot.com"
_COPILOT_HEADERS = {
    "Editor-Version": "vscode/1.95.0",
    "Editor-Plugin-Version": "copilot-chat/0.22.0",
    "Copilot-Integration-Id": "vscode-chat",
    "User-Agent": "GitHubCopilotChat/0.22.0",
}
_COPILOT_REFRESH_MARGIN_S = 120


def _read_copilot_config_tokens() -> list[str]:
    """Read OAuth tokens stored by Copilot editor plugins, if any (some may be stale)."""
    from pathlib import Path

    config_dir = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "github-copilot"
    tokens: list[str] = []
    for name in ("apps.json", "hosts.json"):
        path = config_dir / name
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        for key, entry in data.items():
            if key.startswith("github.com") and isinstance(entry, dict) and entry.get("oauth_token"):
                tokens.append(entry["oauth_token"])
    return tokens


def _read_gh_cli_token() -> str | None:
    import shutil
    import subprocess

    if not shutil.which("gh"):
        return None
    try:
        result = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    token = result.stdout.strip()
    return token if result.returncode == 0 and token else None


def resolve_github_oauth_tokens(api_key: str | None = None) -> list[str]:
    """Return candidate GitHub OAuth tokens that may be entitled to use Copilot, best first.

    Order: explicit argument, ``COPILOT_GITHUB_TOKEN``, Copilot plugin config,
    ``GH_TOKEN`` / ``GITHUB_TOKEN``, then ``gh auth token``.
    """
    sources = (
        lambda: [api_key],
        lambda: [os.environ.get("COPILOT_GITHUB_TOKEN")],
        _read_copilot_config_tokens,
        lambda: [os.environ.get("GH_TOKEN")],
        lambda: [os.environ.get("GITHUB_TOKEN")],
        lambda: [_read_gh_cli_token()],
    )
    tokens: list[str] = []
    for source in sources:
        for token in source():
            token = (token or "").strip()
            if token and token not in tokens:
                tokens.append(token)
    if not tokens:
        raise RuntimeError(
            "No GitHub token found for the Copilot provider. Set COPILOT_GITHUB_TOKEN "
            "(or GH_TOKEN / GITHUB_TOKEN), or run `gh auth login`."
        )
    return tokens


def resolve_github_oauth_token(api_key: str | None = None) -> str:
    """Return the highest-priority candidate token (see ``resolve_github_oauth_tokens``)."""
    return resolve_github_oauth_tokens(api_key)[0]


class CopilotProvider(OpenAIProvider):
    """GitHub Copilot chat API (OpenAI-compatible).

    Exchanges a GitHub OAuth token for a short-lived Copilot API token and
    refreshes it transparently. The token and endpoint are cached per OAuth
    token and shared by every provider instance (analysts run in parallel).
    """

    _lock = threading.Lock()
    _cache: dict[str, dict[str, Any]] = {}

    def __init__(self, model: str, api_key: str | None = None, base_url: str | None = None):
        self.model = model
        self._oauth_tokens = resolve_github_oauth_tokens(api_key)
        self._cache_key = self._oauth_tokens[0]
        self._base_url_override = base_url or None
        self._client_token: str | None = None
        self.client = None
        self._refresh_client()

    def _exchange_token(self) -> dict[str, Any]:
        import httpx

        status = None
        for token in list(self._oauth_tokens):
            resp = httpx.get(
                _COPILOT_TOKEN_URL,
                headers={"Authorization": f"token {token}", "Accept": "application/json", **_COPILOT_HEADERS},
                timeout=30,
            )
            status = resp.status_code
            if status == 200 and resp.json().get("token"):
                # Remember which candidate works so refreshes don't retry stale ones
                self._oauth_tokens.remove(token)
                self._oauth_tokens.insert(0, token)
                return resp.json()
        raise RuntimeError(
            f"Copilot token exchange failed (HTTP {status}). Use a GitHub OAuth token "
            "(e.g. from `gh auth login` or the Copilot plugin login) for an account with an active "
            "Copilot subscription; classic/fine-grained PATs are not accepted."
        )

    def _get_token(self, force: bool = False) -> dict[str, Any]:
        with self._lock:
            entry = self._cache.get(self._cache_key)
            if force or not entry or entry["expires_at"] - time.time() < _COPILOT_REFRESH_MARGIN_S:
                data = self._exchange_token()
                entry = {
                    "token": data["token"],
                    "expires_at": float(data.get("expires_at") or time.time() + 1500),
                    "api": (data.get("endpoints") or {}).get("api"),
                }
                self._cache[self._cache_key] = entry
            return entry

    def _refresh_client(self, force: bool = False) -> None:
        from openai import OpenAI

        entry = self._get_token(force=force)
        if entry["token"] == self._client_token and self.client is not None:
            return
        self.client = OpenAI(
            api_key=entry["token"],
            base_url=self._base_url_override or entry["api"] or _COPILOT_DEFAULT_BASE_URL,
            default_headers=_COPILOT_HEADERS,
        )
        self._client_token = entry["token"]

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
    ) -> LLMResponse:
        self._refresh_client()
        try:
            return super().chat(messages, tools=tools, tool_choice=tool_choice)
        except Exception as exc:
            if getattr(exc, "status_code", None) != 401:
                raise
            self._refresh_client(force=True)
            return super().chat(messages, tools=tools, tool_choice=tool_choice)


# ---------------------------------------------------------------------------
# Retry helper
# ---------------------------------------------------------------------------

def chat_with_retry(
    provider: LLMProvider,
    messages: list[dict[str, Any]],
    tools: list[dict] | None = None,
    tool_choice: str = "auto",
    max_retries: int = 3,
) -> LLMResponse:
    """Call provider.chat() with exponential backoff on transient errors.

    Retries on rate-limit, server (5xx), and network timeout errors.
    Re-raises immediately on authentication (4xx) and other permanent errors.
    Backoff: 2^attempt seconds (2s, 4s, 8s) + up to 1s of random jitter.
    """
    for attempt in range(max_retries + 1):
        try:
            return provider.chat(messages, tools=tools, tool_choice=tool_choice)
        except Exception as exc:
            if attempt == max_retries:
                raise

            exc_type = type(exc).__name__
            exc_module = type(exc).__module__

            # Determine if this is a retryable error
            retryable = False

            # Check anthropic errors
            if "anthropic" in exc_module:
                if "RateLimitError" in exc_type:
                    retryable = True
                elif "APIStatusError" in exc_type:
                    status = getattr(exc, "status_code", 0)
                    retryable = status >= 500
                elif "APIConnectionError" in exc_type or "APITimeoutError" in exc_type:
                    retryable = True

            # Check openai errors
            elif "openai" in exc_module:
                if "RateLimitError" in exc_type:
                    retryable = True
                elif "APIStatusError" in exc_type or "InternalServerError" in exc_type:
                    status = getattr(exc, "status_code", 0)
                    retryable = status >= 500
                elif "APIConnectionError" in exc_type or "APITimeoutError" in exc_type:
                    retryable = True

            # Check httpx errors (underlying transport)
            elif "httpx" in exc_module:
                if any(t in exc_type for t in ("TimeoutException", "ConnectError", "RemoteProtocolError")):
                    retryable = True

            if not retryable:
                raise

            delay = (2 ** attempt) + random.uniform(0, 1)
            time.sleep(delay)

    # Unreachable, but satisfies type checkers
    raise RuntimeError("chat_with_retry: exhausted retries")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def resolve_provider_name(provider: str, model: str, base_url: str = "") -> str:
    """Normalize a provider setting (including ``auto``) to a concrete provider name."""
    provider = (provider or "auto").lower()
    if provider == "anthropic":
        return "claude"
    if provider == "github":
        return "copilot"
    if provider != "auto":
        return provider
    if "githubcopilot.com" in base_url:
        return "copilot"
    if model.startswith("claude") or model.startswith("anthropic"):
        return "claude"
    if "ollama" in base_url or "11434" in base_url:
        return "ollama"
    if base_url:
        return "openai"
    # Default: if model looks like an Ollama model (has ':'), use Ollama
    return "ollama" if ":" in model else "claude"


def uses_anthropic_format(provider: str, model: str, base_url: str = "") -> bool:
    """True when the resolved provider speaks Anthropic-native tool/message formats."""
    return resolve_provider_name(provider, model, base_url) == "claude"


def create_provider(
    provider: str = "auto",
    model: str = "claude-sonnet-4-6",
    base_url: str = "",
    api_key: str | None = None,
    num_ctx: int = 131072,
) -> LLMProvider:
    """Create an LLM provider based on configuration.

    Args:
        provider: "auto", "claude"/"anthropic", "ollama", "openai", or "copilot".
        model: Model name (e.g. "claude-sonnet-4-6", "qwen3.5:35b", "gpt-4.1").
        base_url: API endpoint (required for ollama, optional for openai/copilot).
        api_key: API key (claude/openai) or GitHub OAuth token (copilot); ignored for ollama.

    Auto-detection logic (see ``resolve_provider_name``):
        - base_url contains "githubcopilot.com" → CopilotProvider
        - Model starts with "claude" → ClaudeProvider
        - base_url contains "ollama" or port 11434 → OllamaProvider
        - Otherwise → OpenAIProvider
    """
    name = resolve_provider_name(provider, model, base_url)

    if name == "claude":
        return ClaudeProvider(model=model, api_key=api_key)
    elif name == "ollama":
        url = base_url or "http://127.0.0.1:11434/v1"
        return OllamaProvider(model=model, base_url=url, num_ctx=num_ctx)
    elif name == "openai":
        return OpenAIProvider(model=model, api_key=api_key, base_url=base_url or None)
    elif name == "copilot":
        return CopilotProvider(model=model, api_key=api_key, base_url=base_url or None)
    else:
        raise ValueError(
            f"Unknown provider: {provider!r}. Use 'auto', 'claude', 'ollama', 'openai', or 'copilot'."
        )

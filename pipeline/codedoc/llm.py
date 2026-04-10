"""LLM provider abstraction layer.

Supports Claude (native Anthropic SDK) and Ollama (OpenAI-compatible API).
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

def create_provider(
    provider: str = "auto",
    model: str = "claude-sonnet-4-6",
    base_url: str = "",
    api_key: str | None = None,
    num_ctx: int = 131072,
) -> LLMProvider:
    """Create an LLM provider based on configuration.

    Args:
        provider: "auto", "claude", "ollama", or "openai".
        model: Model name (e.g. "claude-sonnet-4-6", "qwen3.5:35b").
        base_url: API endpoint (required for ollama, optional for openai).
        api_key: API key (required for claude/openai, ignored for ollama).

    Auto-detection logic:
        - Model starts with "claude" → ClaudeProvider
        - base_url contains "ollama" or port 11434 → OllamaProvider
        - Otherwise → OpenAIProvider
    """
    provider = provider.lower()

    if provider == "auto":
        if model.startswith("claude") or model.startswith("anthropic"):
            provider = "claude"
        elif "ollama" in base_url or "11434" in base_url:
            provider = "ollama"
        elif base_url:
            provider = "openai"
        else:
            # Default: if model looks like an Ollama model (has ':'), use Ollama
            provider = "ollama" if ":" in model else "claude"

    if provider == "claude" or provider == "anthropic":
        return ClaudeProvider(model=model, api_key=api_key)
    elif provider == "ollama":
        url = base_url or "http://127.0.0.1:11434/v1"
        return OllamaProvider(model=model, base_url=url, num_ctx=num_ctx)
    elif provider == "openai":
        return OpenAIProvider(model=model, api_key=api_key, base_url=base_url or None)
    else:
        raise ValueError(f"Unknown provider: {provider!r}. Use 'auto', 'claude', 'ollama', or 'openai'.")

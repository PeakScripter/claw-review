"""Groq chat-completions wrapper.

Phase 1 uses non-streaming completions for simplicity: the engine awaits the
full response and converts it into an `AssistantMessage`. Streaming can be
layered on later without changing the engine's interface.

Groq's API is OpenAI-compatible, so the wire shape matches OpenAI tool calling.
"""

from __future__ import annotations

import asyncio
import os
import random
from dataclasses import dataclass
from typing import Any

from review_agent.llm.messages import AssistantMessage, ToolCall


class GroqClientError(RuntimeError):
    pass


class GroqRateLimited(GroqClientError):
    pass


@dataclass
class GroqConfig:
    api_key: str
    model: str
    temperature: float = 0.0
    max_tokens: int = 4096
    base_url: str | None = None  # for self-hosted/proxy setups


class GroqClient:
    """Thin async wrapper. Lazy-imports `groq` so the package is only required
    at runtime, not at test-collection time.
    """

    def __init__(self, config: GroqConfig) -> None:
        self.config = config
        self._client = None  # lazy

    def _ensure_client(self):
        if self._client is None:
            try:
                from groq import Groq
            except ImportError as e:
                raise GroqClientError(
                    "The `groq` package is required. Install with `pip install groq`."
                ) from e
            kwargs: dict[str, Any] = {"api_key": self.config.api_key}
            if self.config.base_url:
                kwargs["base_url"] = self.config.base_url
            self._client = Groq(**kwargs)
        return self._client

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> AssistantMessage:
        """One chat-completion turn. Retries on transient rate limits."""
        client = self._ensure_client()

        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        last_exc: Exception | None = None
        for attempt in range(4):
            try:
                # groq SDK is sync; run in a thread to keep the engine async.
                response = await asyncio.to_thread(
                    client.chat.completions.create, **kwargs
                )
                return _parse_response(response)
            except Exception as e:
                last_exc = e
                if not _is_rate_limit(e):
                    raise GroqClientError(f"Groq call failed: {e}") from e
                # Exponential backoff with jitter for 429s.
                delay = min(2 ** attempt + random.random(), 30.0)
                await asyncio.sleep(delay)

        raise GroqRateLimited(
            f"Groq rate-limited after retries: {last_exc}"
        ) from last_exc


def _is_rate_limit(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    if "ratelimit" in name:
        return True
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    return status == 429


def _parse_response(response: Any) -> AssistantMessage:
    """Convert a Groq ChatCompletion into our typed AssistantMessage."""
    if not response.choices:
        raise GroqClientError("Groq returned no choices.")
    choice = response.choices[0]
    msg = choice.message

    tool_calls: list[ToolCall] = []
    raw_calls = getattr(msg, "tool_calls", None) or []
    for tc in raw_calls:
        tool_calls.append(
            ToolCall(
                id=tc.id,
                name=tc.function.name,
                arguments=tc.function.arguments or "{}",
            )
        )

    return AssistantMessage(content=msg.content or "", tool_calls=tool_calls)


def config_from_env(model: str) -> GroqConfig:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise GroqClientError(
            "GROQ_API_KEY environment variable is not set. "
            "Get a free key at https://console.groq.com/keys."
        )
    return GroqConfig(api_key=api_key, model=model)

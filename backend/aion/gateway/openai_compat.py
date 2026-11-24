"""OpenAI-compatible adapter (OpenAI and self-hosted relays)."""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from typing import Any, Literal

import httpx

from aion.gateway.base import (
    ChatMessage,
    ModelResponse,
    ProviderError,
    estimate_cost,
)
from aion.gateway.transport import post_json

ResponseFormat = Literal["json_object", "json_schema", "none"]


class OpenAICompatProvider:
    """Works with OpenAI and any endpoint exposing ``/chat/completions``."""

    name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
        max_attempts: int = 3,
        backoff_base: float = 0.5,
        response_format: ResponseFormat = "json_object",
        client: httpx.AsyncClient | None = None,
        name: str | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("openai api_key is required")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base
        self._response_format = response_format
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(transport=transport, timeout=timeout)
        if name:
            self.name = name

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str,
        max_tokens: int,
        temperature: float,
        json_schema: dict[str, Any] | None = None,
    ) -> ModelResponse:
        turns = [{"role": m.role, "content": m.content} for m in messages]
        if json_schema is not None:
            schema_text = json.dumps(json_schema, ensure_ascii=False, sort_keys=True)
            turns.insert(
                0,
                {
                    "role": "system",
                    "content": f"只输出一个 JSON 对象，必须严格满足以下 JSON Schema：\n{schema_text}",
                },
            )

        payload: dict[str, Any] = {
            "model": model,
            "messages": turns,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if json_schema is not None and self._response_format != "none":
            payload["response_format"] = _response_format(self._response_format, json_schema)

        headers = {
            "authorization": f"Bearer {self._api_key}",
            "content-type": "application/json",
        }
        started = time.perf_counter()
        response = await post_json(
            self._client,
            f"{self._base_url}/chat/completions",
            headers=headers,
            payload=payload,
            request_timeout=self._timeout,
            provider=self.name,
            max_attempts=self._max_attempts,
            backoff_base=self._backoff_base,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)

        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError(self.name, "malformed JSON response", retryable=False) from exc

        text = _extract_text(data, self.name)
        usage = data.get("usage") or {}
        input_tokens = int(usage.get("prompt_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or 0)
        return ModelResponse(
            text=text,
            model=str(data.get("model") or model),
            provider=self.name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=estimate_cost(model, input_tokens, output_tokens),
            latency_ms=latency_ms,
            raw=data if isinstance(data, dict) else {},
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _response_format(mode: ResponseFormat, schema: dict[str, Any]) -> dict[str, Any]:
    if mode == "json_schema":
        return {
            "type": "json_schema",
            "json_schema": {"name": "agent_output", "strict": True, "schema": schema},
        }
    return {"type": "json_object"}


def _extract_text(data: Any, provider: str) -> str:
    if not isinstance(data, dict):
        raise ProviderError(provider, "unexpected response shape", retryable=False)
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ProviderError(provider, "missing choices", retryable=False)
    first = choices[0]
    message = first.get("message") if isinstance(first, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise ProviderError(provider, "empty message content", retryable=False)
    return content.strip()

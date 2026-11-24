"""Anthropic Messages API adapter."""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from typing import Any

import httpx

from aion.gateway.base import (
    ChatMessage,
    ModelResponse,
    ProviderError,
    estimate_cost,
)
from aion.gateway.transport import post_json

ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.anthropic.com",
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
        max_attempts: int = 3,
        backoff_base: float = 0.5,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("anthropic api_key is required")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(transport=transport, timeout=timeout)

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str,
        max_tokens: int,
        temperature: float,
        json_schema: dict[str, Any] | None = None,
    ) -> ModelResponse:
        system_text, turns = _split_messages(messages)
        if json_schema is not None:
            schema_text = json.dumps(json_schema, ensure_ascii=False, sort_keys=True)
            system_text = (
                f"{system_text}\n\n只输出一个 JSON 对象，必须严格满足以下 JSON Schema：\n{schema_text}"
            ).strip()

        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": turns,
        }
        if system_text:
            payload["system"] = system_text

        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        started = time.perf_counter()
        response = await post_json(
            self._client,
            f"{self._base_url}/v1/messages",
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

        text = _extract_text(data)
        usage = data.get("usage") or {}
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
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


def _split_messages(messages: Sequence[ChatMessage]) -> tuple[str, list[dict[str, str]]]:
    system_parts: list[str] = []
    turns: list[dict[str, str]] = []
    for message in messages:
        if message.role == "system":
            system_parts.append(message.content)
        else:
            turns.append({"role": message.role, "content": message.content})
    if not turns:
        turns.append({"role": "user", "content": ""})
    return "\n\n".join(system_parts), turns


def _extract_text(data: Any) -> str:
    if not isinstance(data, dict):
        raise ProviderError("anthropic", "unexpected response shape", retryable=False)
    blocks = data.get("content")
    if not isinstance(blocks, list):
        raise ProviderError("anthropic", "missing content blocks", retryable=False)
    parts = [
        str(block.get("text", ""))
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    text = "".join(parts).strip()
    if not text:
        raise ProviderError("anthropic", "empty text content", retryable=False)
    return text

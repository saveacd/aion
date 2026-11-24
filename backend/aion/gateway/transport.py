"""HTTP retry helper shared by provider adapters.

Only this module imports ``httpx`` so the rest of the gateway stays testable
without the networking stack installed.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from aion.gateway.base import ProviderError

RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


async def post_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    request_timeout: float,
    provider: str,
    max_attempts: int = 3,
    backoff_base: float = 0.5,
) -> httpx.Response:
    """POST JSON with bounded exponential-backoff retries.

    Retries 429/5xx and transport errors up to ``max_attempts``. Error messages
    contain only the status code / exception type, never headers or bodies.
    """

    attempts = max(1, min(int(max_attempts), 5))
    for attempt in range(1, attempts + 1):
        try:
            response = await client.post(
                url, headers=headers, json=payload, timeout=request_timeout
            )
        except httpx.RequestError as exc:
            if attempt >= attempts:
                raise ProviderError(
                    provider, f"transport error ({type(exc).__name__})", retryable=True
                ) from exc
            await asyncio.sleep(backoff_base * (2 ** (attempt - 1)))
            continue

        if response.status_code in RETRYABLE_STATUS:
            if attempt >= attempts:
                raise ProviderError(
                    provider,
                    f"HTTP {response.status_code}",
                    retryable=True,
                    status_code=response.status_code,
                )
            delay = min(_retry_after(response) or backoff_base * (2 ** (attempt - 1)), 10.0)
            await asyncio.sleep(delay)
            continue

        if response.status_code >= 400:
            raise ProviderError(
                provider,
                f"HTTP {response.status_code}",
                retryable=False,
                status_code=response.status_code,
            )
        return response

    # Unreachable because the loop either returns or raises.
    raise ProviderError(provider, "retry budget exhausted", retryable=True)


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        return None
    return max(0.0, min(seconds, 10.0))

"""Local fakes so no test ever touches Redis, a database or the network."""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

from aion.gateway.base import ChatMessage, ModelResponse


class FakeClock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeRedis:
    """Tiny subset of redis.asyncio used by BudgetLedger and CircuitBreaker."""

    def __init__(self, clock: Any | None = None) -> None:
        self._data: dict[str, Any] = {}
        self._expires: dict[str, float] = {}
        self._zsets: dict[str, dict[str, float]] = {}
        self._clock = clock or time.time

    def _alive(self, key: str) -> bool:
        deadline = self._expires.get(key)
        if deadline is None:
            return True
        if deadline <= self._clock():
            self._data.pop(key, None)
            self._expires.pop(key, None)
            return False
        return True

    async def get(self, key: str) -> Any:
        if not self._alive(key):
            return None
        return self._data.get(key)

    async def set(
        self,
        key: str,
        value: Any,
        *,
        nx: bool = False,
        ex: int | None = None,
    ) -> bool:
        if nx and self._alive(key) and key in self._data:
            return False
        self._data[key] = value
        if ex is not None:
            self._expires[key] = self._clock() + ex
        else:
            self._expires.pop(key, None)
        return True

    async def exists(self, key: str) -> int:
        if not self._alive(key):
            return 0
        return 1 if key in self._data or key in self._zsets else 0

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if key in self._data or key in self._zsets:
                removed += 1
            self._data.pop(key, None)
            self._zsets.pop(key, None)
            self._expires.pop(key, None)
        return removed

    async def expire(self, key: str, seconds: int) -> bool:
        if key not in self._data and key not in self._zsets:
            return False
        self._expires[key] = self._clock() + seconds
        return True

    async def incrbyfloat(self, key: str, amount: float) -> float:
        if not self._alive(key):
            self._data.pop(key, None)
        current = float(self._data.get(key, 0))
        value = current + float(amount)
        self._data[key] = value
        return value

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        bucket = self._zsets.setdefault(key, {})
        bucket.update({member: float(score) for member, score in mapping.items()})
        return len(mapping)

    async def zremrangebyscore(self, key: str, minimum: Any, maximum: Any) -> int:
        bucket = self._zsets.get(key)
        if not bucket:
            return 0
        low = _score(minimum)
        high = _score(maximum)
        doomed = [member for member, score in bucket.items() if low <= score <= high]
        for member in doomed:
            bucket.pop(member, None)
        return len(doomed)

    async def zcard(self, key: str) -> int:
        return len(self._zsets.get(key, {}))


def _score(value: Any) -> float:
    if isinstance(value, str):
        if value == "-inf":
            return float("-inf")
        if value in ("+inf", "inf"):
            return float("inf")
    return float(value)


class FakeProvider:
    """Returns canned texts (or raises canned exceptions) and records calls."""

    def __init__(
        self,
        responses: list[Any],
        *,
        name: str = "fake",
        input_tokens: int = 10,
        output_tokens: int = 5,
        cost_usd: Decimal = Decimal("0.0001"),
    ) -> None:
        self.name = name
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cost_usd = cost_usd

    async def complete(
        self,
        messages: list[ChatMessage] | tuple[ChatMessage, ...],
        *,
        model: str,
        max_tokens: int,
        temperature: float,
        json_schema: dict[str, Any] | None = None,
    ) -> ModelResponse:
        index = min(len(self.calls), len(self.responses) - 1)
        self.calls.append(
            {
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "has_schema": json_schema is not None,
            }
        )
        item = self.responses[index]
        if isinstance(item, BaseException):
            raise item
        return ModelResponse(
            text=str(item),
            model=model,
            provider=self.name,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cost_usd=self.cost_usd,
            latency_ms=1,
            raw={},
        )


class FakeSession:
    def __init__(self, sink: list[Any]) -> None:
        self._sink = sink

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_: Any) -> bool:
        return False

    def add(self, obj: Any) -> None:
        self._sink.append(obj)

    async def commit(self) -> None:
        return None


class FakeSessionFactory:
    def __init__(self) -> None:
        self.objects: list[Any] = []

    def __call__(self) -> FakeSession:
        return FakeSession(self.objects)

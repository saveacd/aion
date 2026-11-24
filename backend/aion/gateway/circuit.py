"""Redis-backed sliding-window circuit breaker.

When a provider keeps failing the breaker opens for a cooldown. After the
cooldown a single half-open probe is allowed; its result either closes the
breaker or re-opens it. There is deliberately no automatic model fallback:
the orchestrator degrades to ``NO_TRADE`` instead of guessing.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any

CLOSED = "CLOSED"
OPEN = "OPEN"
HALF_OPEN = "HALF_OPEN"


class CircuitOpen(RuntimeError):
    """Raised when a provider is currently blocked by its circuit breaker."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"circuit open for provider={name}")


class CircuitBreaker:
    def __init__(
        self,
        redis: Any,
        *,
        failure_threshold: int = 5,
        window_seconds: float = 60.0,
        open_seconds: float = 60.0,
        half_open_ttl_seconds: float = 30.0,
        key_prefix: str = "aion:circuit",
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._redis = redis
        self.failure_threshold = max(1, int(failure_threshold))
        self.window_seconds = float(window_seconds)
        self.open_seconds = float(open_seconds)
        self.half_open_ttl_seconds = float(half_open_ttl_seconds)
        self._prefix = key_prefix
        self._clock = clock or time.time

    def _now(self) -> float:
        return self._clock()

    def _fails_key(self, name: str) -> str:
        return f"{self._prefix}:{name}:failures"

    def _open_key(self, name: str) -> str:
        return f"{self._prefix}:{name}:open_until"

    def _probe_key(self, name: str) -> str:
        return f"{self._prefix}:{name}:probe"

    async def failure_count(self, name: str) -> int:
        """Failures inside the sliding window, pruning stale entries."""

        key = self._fails_key(name)
        cutoff = self._now() - self.window_seconds
        await self._redis.zremrangebyscore(key, "-inf", cutoff)
        return int(await self._redis.zcard(key))

    async def _open_until(self, name: str) -> float | None:
        raw = await self._redis.get(self._open_key(name))
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    async def state(self, name: str) -> str:
        until = await self._open_until(name)
        if until is not None:
            return OPEN if until > self._now() else HALF_OPEN
        if await self.failure_count(name) >= self.failure_threshold:
            return HALF_OPEN
        return CLOSED

    async def allow(self, name: str) -> bool:
        """Return whether a call may proceed. Half-open allows one probe."""

        until = await self._open_until(name)
        if until is not None and until > self._now():
            return False
        if until is None and await self.failure_count(name) < self.failure_threshold:
            return True
        return bool(
            await self._redis.set(
                self._probe_key(name), "1", nx=True, ex=int(self.half_open_ttl_seconds)
            )
        )

    async def record_success(self, name: str) -> None:
        await self._redis.delete(
            self._fails_key(name), self._open_key(name), self._probe_key(name)
        )

    async def record_failure(self, name: str) -> None:
        now = self._now()
        key = self._fails_key(name)
        member = f"{now:.6f}:{uuid.uuid4().hex}"
        await self._redis.zadd(key, {member: now})
        await self._redis.zremrangebyscore(key, "-inf", now - self.window_seconds)
        await self._redis.expire(key, int(self.window_seconds * 2) + 1)
        if await self.failure_count(name) >= self.failure_threshold:
            await self._redis.set(
                self._open_key(name),
                str(now + self.open_seconds),
                ex=int(self.open_seconds * 2) + 1,
            )
            await self._redis.delete(self._probe_key(name))

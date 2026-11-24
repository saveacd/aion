"""Redis connection + shared primitives (rate limiting, locks, pub/sub)."""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncIterator
from typing import Any

import redis.asyncio as aioredis

from aion.core.config import Settings

_client: aioredis.Redis | None = None


def build_client(settings: Settings) -> aioredis.Redis:
    return aioredis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
        health_check_interval=30,
        socket_timeout=5.0,
        socket_connect_timeout=5.0,
        retry_on_timeout=True,
    )


def init_client(settings: Settings) -> aioredis.Redis:
    global _client
    if _client is None:
        _client = build_client(settings)
    return _client


def get_redis() -> aioredis.Redis:
    if _client is None:
        raise RuntimeError("Redis client is not initialised")
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def ping() -> bool:
    try:
        return bool(await get_redis().ping())
    except Exception:
        return False


class RateLimiter:
    """Fixed-window counter. Cheap and adequate for a single-user system."""

    def __init__(self, redis: aioredis.Redis, namespace: str = "rl") -> None:
        self._redis = redis
        self._ns = namespace

    async def hit(self, key: str, *, limit: int, window_seconds: int) -> tuple[bool, int]:
        """Return (allowed, remaining)."""
        bucket = f"{self._ns}:{key}"
        pipe = self._redis.pipeline()
        pipe.incr(bucket)
        pipe.ttl(bucket)
        count, ttl = await pipe.execute()
        if ttl is None or int(ttl) < 0:
            await self._redis.expire(bucket, window_seconds)
            ttl = window_seconds
        remaining = max(0, limit - int(count))
        return int(count) <= limit, remaining

    async def reset(self, key: str) -> None:
        await self._redis.delete(f"{self._ns}:{key}")


@contextlib.asynccontextmanager
async def distributed_lock(
    name: str, *, ttl_seconds: int = 30, wait_seconds: float = 5.0
) -> AsyncIterator[bool]:
    """Redis lock with ownership-safe release and periodic lease renewal."""
    import asyncio

    redis = get_redis()
    token = uuid.uuid4().hex
    key = f"aion:lock:{name}"
    acquired = False
    deadline = asyncio.get_running_loop().time() + wait_seconds
    while True:
        if await redis.set(key, token, nx=True, ex=ttl_seconds):
            acquired = True
            break
        if asyncio.get_running_loop().time() >= deadline:
            break
        await asyncio.sleep(0.1)

    async def renew() -> None:
        script = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
            return redis.call('expire', KEYS[1], ARGV[2])
        end
        return 0
        """
        while True:
            await asyncio.sleep(max(1, ttl_seconds // 3))
            if not await redis.eval(script, 1, key, token, ttl_seconds):
                return

    renew_task = asyncio.create_task(renew()) if acquired else None
    try:
        yield acquired
    finally:
        if renew_task is not None:
            renew_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await renew_task
        if acquired:
            script = """
            if redis.call('get', KEYS[1]) == ARGV[1] then
                return redis.call('del', KEYS[1])
            end
            return 0
            """
            with contextlib.suppress(Exception):
                await redis.eval(script, 1, key, token)


async def publish(channel: str, payload: dict[str, Any]) -> None:
    import json

    await get_redis().publish(channel, json.dumps(payload, default=str))

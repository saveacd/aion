"""In-process event bus.

The bus fans out live events (market, system, agent, order) to subscribers such
as the WebSocket endpoint. It is deliberately memory-only: PostgreSQL remains
the source of truth for anything that must survive a restart.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
from typing import Any

_DEFAULT_QUEUE_SIZE = 256


class EventBus:
    def __init__(self, *, queue_size: int = _DEFAULT_QUEUE_SIZE) -> None:
        self._queue_size = queue_size
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._lock = asyncio.Lock()

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._queue_size)
        async with self._lock:
            self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

    def publish_nowait(self, event: dict[str, Any]) -> int:
        """Deliver without blocking. Slow consumers lose their oldest event."""
        delivered = 0
        for queue in list(self._subscribers):
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)
                delivered += 1
        return delivered

    async def publish(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        symbol: str | None = None,
    ) -> int:
        event: dict[str, Any] = {
            "type": event_type,
            "ts": dt.datetime.now(dt.UTC).isoformat(),
            "data": payload,
        }
        if symbol:
            event["symbol"] = symbol
        return self.publish_nowait(event)


bus = EventBus()


async def publish_event(
    event_type: str,
    payload: dict[str, Any],
    *,
    symbol: str | None = None,
) -> int:
    """Convenience wrapper used by API modules and background services."""
    return await bus.publish(event_type, payload, symbol=symbol)


__all__ = ["EventBus", "bus", "publish_event"]

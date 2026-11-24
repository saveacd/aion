"""User Data Stream handling.

Connecting, reconnecting, recreating listen keys and parsing events live here.
Tests exercise :func:`parse_event` offline; nothing in this module opens a
socket at import time (``websockets`` is imported lazily).
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from aion.exchange.errors import ExchangeError
from aion.exchange.orders import to_decimal

D = Decimal

WS_BASE_URL = "wss://fstream.binance.com/ws"


class StreamError(ExchangeError):
    """The user data stream could not be (re)established."""


@dataclass(frozen=True, slots=True)
class OrderUpdate:
    order_id: str
    client_order_id: str | None
    symbol: str
    side: str
    order_type: str
    status: str
    execution_type: str
    orig_qty: Decimal
    cum_filled_qty: Decimal
    avg_price: Decimal
    last_filled_qty: Decimal
    last_price: Decimal
    reduce_only: bool
    close_position: bool
    position_side: str | None
    trade_time: dt.datetime | None = None
    rejection_reason: str | None = None
    trade_id: str | None = None
    commission: Decimal = D("0")
    commission_asset: str | None = None
    realized_pnl: Decimal = D("0")
    is_maker: bool = False


@dataclass(frozen=True, slots=True)
class UserDataEvent:
    event_type: str
    event_time: dt.datetime | None
    order: OrderUpdate | None = None
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)


def _ms_to_dt(value: Any) -> dt.datetime | None:
    if value is None:
        return None
    return dt.datetime.fromtimestamp(int(value) / 1000, tz=dt.UTC)


def _parse_order_update(payload: dict[str, Any]) -> OrderUpdate:
    return OrderUpdate(
        order_id=str(payload.get("i", "")),
        client_order_id=payload.get("c"),
        symbol=str(payload.get("s", "")),
        side=str(payload.get("S", "")).upper(),
        order_type=str(payload.get("o", "")).upper(),
        status=str(payload.get("X", "")).upper(),
        execution_type=str(payload.get("x", "")).upper(),
        orig_qty=to_decimal(payload.get("q")),
        cum_filled_qty=to_decimal(payload.get("z")),
        avg_price=to_decimal(payload.get("ap")),
        last_filled_qty=to_decimal(payload.get("l")),
        last_price=to_decimal(payload.get("L")),
        reduce_only=bool(payload.get("R", False)),
        close_position=bool(payload.get("cp", False)),
        position_side=payload.get("ps"),
        trade_time=_ms_to_dt(payload.get("T")),
        rejection_reason=payload.get("r"),
        trade_id=(str(payload.get("t")) if payload.get("t") not in (None, -1, "-1") else None),
        commission=to_decimal(payload.get("n")),
        commission_asset=payload.get("N"),
        realized_pnl=to_decimal(payload.get("rp")),
        is_maker=bool(payload.get("m", False)),
    )


def parse_event(payload: str | bytes | dict[str, Any]) -> UserDataEvent:
    """Parse a raw user-data-stream frame into a dataclass."""
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        try:
            data = json.loads(payload)
        except ValueError as exc:
            raise StreamError("user data stream sent invalid JSON") from exc
    else:
        data = payload

    if not isinstance(data, dict):
        raise StreamError("user data stream payload is not an object")

    event_type = str(data.get("e") or "UNKNOWN")
    order = None
    if event_type == "ORDER_TRADE_UPDATE" and isinstance(data.get("o"), dict):
        order = _parse_order_update(data["o"])
    return UserDataEvent(
        event_type=event_type,
        event_time=_ms_to_dt(data.get("E")),
        order=order,
        raw=data,
    )


class UserDataStream:
    """Listen-key lifecycle plus a reconnecting async event iterator."""

    def __init__(
        self,
        client: Any,
        *,
        ws_base_url: str = WS_BASE_URL,
        max_reconnects: int = 10,
        backoff_base_seconds: float = 1.0,
        max_backoff_seconds: float = 60.0,
        keepalive_interval_seconds: float = 25 * 60,
        sleep_fn: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        if max_reconnects < 1:
            raise ValueError("max_reconnects must be >= 1")
        self._client = client
        self._ws_base_url = ws_base_url.rstrip("/")
        self._max_reconnects = int(max_reconnects)
        self._backoff_base = float(backoff_base_seconds)
        self._max_backoff = float(max_backoff_seconds)
        self._keepalive_interval = float(keepalive_interval_seconds)
        self._sleep_fn = sleep_fn or asyncio.sleep

    async def create_listen_key(self) -> str:
        payload = await self._client._api_key_request("POST", "/fapi/v1/listenKey")
        key = payload.get("listenKey") if isinstance(payload, dict) else None
        if not key:
            raise StreamError("exchange did not return a listenKey")
        return str(key)

    async def keepalive(self, listen_key: str) -> None:
        await self._client._api_key_request(
            "PUT", "/fapi/v1/listenKey", {"listenKey": listen_key}
        )

    async def close(self, listen_key: str) -> None:
        await self._client._api_key_request(
            "DELETE", "/fapi/v1/listenKey", {"listenKey": listen_key}
        )

    async def _keepalive_loop(self, listen_key: str) -> None:
        while True:
            await asyncio.sleep(self._keepalive_interval)
            try:
                await self.keepalive(listen_key)
            except (ExchangeError, OSError):
                # A failed keepalive means the stream will be recreated on reconnect.
                return

    async def user_data_stream(self, listen_key: str | None = None) -> AsyncIterator[UserDataEvent]:
        """Yield parsed events, reconnecting with bounded exponential backoff."""
        from websockets.asyncio.client import connect
        from websockets.exceptions import ConnectionClosed

        key = listen_key or await self.create_listen_key()
        attempt = 0

        while True:
            if attempt > self._max_reconnects:
                raise StreamError(
                    f"user data stream exceeded {self._max_reconnects} reconnect attempts"
                )
            if attempt:
                delay = min(self._backoff_base * (2 ** (attempt - 1)), self._max_backoff)
                await self._sleep_fn(delay)

            keepalive_task: asyncio.Task[None] | None = None
            expired = False
            try:
                async with connect(
                    f"{self._ws_base_url}/{key}",
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=8 * 1024 * 1024,
                ) as websocket:
                    attempt = 0
                    keepalive_task = asyncio.create_task(self._keepalive_loop(key))
                    async for raw in websocket:
                        event = parse_event(raw)
                        if event.event_type in ("listenKeyExpired", "LISTEN_KEY_EXPIRED"):
                            expired = True
                            break
                        yield event
            except (OSError, ConnectionClosed, TimeoutError):
                attempt += 1
            finally:
                if keepalive_task is not None:
                    keepalive_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await keepalive_task

            if expired:
                attempt = 0
            else:
                attempt += 1

            # The previous key may be expired or bound to the dead connection.
            try:
                key = await self.create_listen_key()
            except ExchangeError:
                attempt += 1

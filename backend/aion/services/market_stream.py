"""Live public market data: Binance USDⓈ-M ticker WebSocket + on-demand klines.

The REST ingestion loop still persists snapshots/candles for history. This
module adds a 1-second in-memory price feed so the API, the AI inputs and the
entry-plan watcher can work with the current price instead of a stale snapshot.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import logging
from collections.abc import Iterable
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)

WS_BASE = "wss://fstream.binance.com"
FAPI = "https://fapi.binance.com"
D = Decimal

#: symbol -> {"price": Decimal, "change_pct": Decimal | None, "ts": datetime}
_PRICES: dict[str, dict[str, Any]] = {}
_EVENTS: dict[str, dict[str, dict[str, Any]]] = {}


def latest_price(symbol: str) -> dict[str, Any] | None:
    """Newest tick for ``symbol`` (None when the feed has not seen it yet)."""
    return _PRICES.get(symbol.upper())


def all_prices() -> list[dict[str, Any]]:
    now = dt.datetime.now(dt.UTC)
    return [
        {
            "symbol": symbol,
            "price": str(item["price"]),
            "change_pct_24h": (
                str(item["change_pct"]) if item.get("change_pct") is not None else None
            ),
            "captured_at": item["ts"].isoformat(),
            "age_ms": int((now - item["ts"]).total_seconds() * 1000),
        }
        for symbol, item in sorted(_PRICES.items())
    ]


def _dec(value: Any) -> Decimal | None:
    try:
        number = D(str(value))
        return number if number.is_finite() else None
    except Exception:  # noqa: BLE001
        return None


async def fetch_live_klines(
    symbol: str, interval: str = "1m", limit: int = 60, timeout_seconds: float = 6.0
) -> list[dict[str, Any]]:
    """Fetch the freshest klines straight from Binance (includes the forming bar)."""
    try:
        async with httpx.AsyncClient(base_url=FAPI, timeout=timeout_seconds) as client:
            response = await client.get(
                "/fapi/v1/klines",
                params={"symbol": symbol.upper(), "interval": interval, "limit": limit},
            )
            response.raise_for_status()
            rows = response.json()
    except Exception as exc:  # noqa: BLE001 - realtime is best-effort, never fatal
        logger.warning("live klines unavailable for %s: %s", symbol, exc)
        return []
    bars: list[dict[str, Any]] = []
    for row in rows:
        try:
            bars.append(
                {
                    "t": dt.datetime.fromtimestamp(int(row[0]) / 1000, tz=dt.UTC).isoformat(),
                    "o": str(row[1]),
                    "h": str(row[2]),
                    "l": str(row[3]),
                    "c": str(row[4]),
                    "v": str(row[5]),
                }
            )
        except (IndexError, TypeError, ValueError):
            continue
    return bars


async def _symbols(session_factory: async_sessionmaker[AsyncSession], base: Iterable[str]) -> set[str]:
    wanted = {s.upper() for s in base if s}
    try:
        from aion.models import AutopilotConfig

        async with session_factory() as db:
            rows = (await db.execute(select(AutopilotConfig.symbols))).scalars().all()
        for row in rows:
            for symbol in row or []:
                if str(symbol).strip():
                    wanted.add(str(symbol).strip().upper())
    except Exception:  # noqa: BLE001 - feed must start even if the query fails
        logger.exception("failed to read autopilot symbols for live feed")
    return wanted


def _event_time(milliseconds: Any) -> dt.datetime:
    return dt.datetime.fromtimestamp(int(milliseconds) / 1000, tz=dt.UTC)


def _fresh(timestamp: dt.datetime, seconds: int) -> bool:
    return -1 <= (dt.datetime.now(dt.UTC) - timestamp).total_seconds() <= seconds


def _save_price(symbol: str, price: Decimal, change: Decimal | None, timestamp: dt.datetime) -> None:
    current = _PRICES.get(symbol)
    if price <= 0 or not _fresh(timestamp, 30) or (current and current["ts"] > timestamp):
        return
    if change is None and current:
        change = current.get("change_pct")
    _PRICES[symbol] = {"price": price, "change_pct": change, "ts": timestamp}


def process_market_event(data: dict[str, Any]) -> None:
    """Record a fresh, ordered public ticker or stream sample."""
    event = data.get("e")
    if event not in {"24hrTicker", "aggTrade", "depthUpdate", "forceOrder"}:
        return
    order = data.get("o") if event == "forceOrder" else None
    symbol = str((order or data).get("s", "")).upper()
    timestamp = _event_time(data["E"])
    if not symbol or not _fresh(timestamp, 30):
        return
    if event == "24hrTicker":
        price = _dec(data.get("c"))
        if price is not None:
            _save_price(symbol, price, _dec(data.get("P")), timestamp)
        return

    events = _EVENTS.setdefault(symbol, {})
    current = events.get(event)
    event_id = int(data["a"] if event == "aggTrade" else data.get("u", data["E"]))
    if current and (timestamp < current["ts"] or event_id <= current["event_id"]):
        return
    values: dict[str, Any]
    if event == "aggTrade":
        price, quantity = _dec(data.get("p")), _dec(data.get("q"))
        if price is None or quantity is None or min(price, quantity) <= 0:
            return
        values = {"price": str(price), "quantity": str(quantity), "buyer_is_maker": data.get("m")}
        _save_price(symbol, price, None, timestamp)
    elif event == "depthUpdate":
        bids, asks = data["b"], data["a"]
        if not bids or not asks:
            return
        bid, ask = _dec(bids[0][0]), _dec(asks[0][0])
        if bid is None or ask is None or not 0 < bid < ask:
            return
        values = {"bids": bids, "asks": asks}
    else:
        values = {name: (order or {}).get(key) for name, key in (
            ("side", "S"), ("average_price", "ap"), ("filled_quantity", "z"),
        )}
    events[event] = {"ts": timestamp, "event_id": event_id, **values}


def recent_market_events(symbol: str) -> dict[str, dict[str, Any]]:
    return {
        kind: {key: value for key, value in event.items() if key != "ts"} | {"captured_at": event["ts"].isoformat()}
        for kind, event in _EVENTS.get(symbol.upper(), {}).items()
        if _fresh(event["ts"], 60 if kind == "forceOrder" else 10)
    }


async def _consume(symbols: set[str]) -> None:
    async with asyncio.TaskGroup() as group:
        group.create_task(_consume_route(symbols, "market", ("ticker", "aggTrade", "forceOrder")))
        group.create_task(_consume_route(symbols, "public", ("depth10@500ms",)))


async def _consume_route(symbols: set[str], route: str, topics: tuple[str, ...]) -> None:
    from websockets.asyncio.client import connect

    streams = "/".join(f"{symbol.lower()}@{topic}" for symbol in sorted(symbols) for topic in topics)
    url = f"{WS_BASE}/{route}/stream?streams={streams}"
    async with connect(url, ping_interval=20, ping_timeout=20, open_timeout=10, max_size=4 * 1024 * 1024) as ws:
        async for raw in ws:
            try:
                payload = json.loads(raw)
                process_market_event(payload.get("data") or payload)
            except (ValueError, TypeError, KeyError, AttributeError, IndexError, OverflowError):
                continue
    raise ConnectionError(f"Binance {route} market stream closed")


async def run_forever(
    session_factory: async_sessionmaker[AsyncSession], base_symbols: Iterable[str]
) -> None:
    """Keep live prices fresh.

    Two independent sources: the Binance ticker WebSocket (≈1 update/second)
    and a REST 24h-ticker poll (every 2 seconds). Some networks block the
    WebSocket handshake while allowing REST, so the poller guarantees the feed
    still works there; whichever source is available keeps winning writes.
    """
    await asyncio.gather(
        _poll_prices_forever(session_factory, list(base_symbols)),
        _ws_forever(session_factory, list(base_symbols)),
    )


async def _poll_prices_forever(
    session_factory: async_sessionmaker[AsyncSession], base: list[str]
) -> None:
    refresh_every = 30  # re-read configured symbols about once a minute
    ticks = 0
    symbols: list[str] = []
    async with httpx.AsyncClient(base_url=FAPI, timeout=5.0) as client:
        while True:
            if not symbols or ticks % refresh_every == 0:
                symbols = sorted(await _symbols(session_factory, base))
                for key in list(_PRICES):
                    if key not in symbols:
                        _PRICES.pop(key, None)
            if symbols:
                if not await _poll_last_prices(client, symbols):
                    ticks += 1
                    await asyncio.sleep(1.0)
                    continue
                if ticks % 10 == 0:
                    await _poll_24h_stats(client, symbols)
            ticks += 1
            await asyncio.sleep(1.0)


async def _poll_last_prices(client: httpx.AsyncClient, symbols: list[str]) -> bool:
    """Refresh last prices every second (the 24h stats change much slower).

    ``/fapi/v1/ticker/price`` only honours a single ``symbol`` parameter; the
    batch ``symbols`` form silently returns the whole market, so request each
    configured symbol in parallel instead.
    """

    async def one(symbol: str) -> None:
        response = await client.get("/fapi/v1/ticker/price", params={"symbol": symbol})
        response.raise_for_status()
        data = response.json()
        price = _dec(data.get("price") if isinstance(data, dict) else None)
        if price is None:
            return
        ts = _event_time(data["time"]) if data.get("time") else dt.datetime.now(dt.UTC)
        _save_price(symbol, price, None, ts)

    results = await asyncio.gather(*(one(s) for s in symbols), return_exceptions=True)
    errors = [str(r) for r in results if isinstance(r, BaseException)]
    if errors:
        logger.warning("live price poll failed: %s", errors[0])
        return False
    return True


async def _poll_24h_stats(client: httpx.AsyncClient, symbols: list[str]) -> None:
    """Refresh 24h change per symbol (batch params are ignored by this API)."""

    async def one(symbol: str) -> None:
        response = await client.get("/fapi/v1/ticker/24hr", params={"symbol": symbol})
        response.raise_for_status()
        data = response.json()
        price = _dec(data.get("lastPrice") if isinstance(data, dict) else None)
        change = _dec(data.get("priceChangePercent") if isinstance(data, dict) else None)
        if price is None:
            return
        ts = _event_time(data["closeTime"]) if data.get("closeTime") else dt.datetime.now(dt.UTC)
        current = _PRICES.get(symbol)
        if current and current["ts"] > ts and change is not None:
            current["change_pct"] = change
        else:
            _save_price(symbol, price, change, ts)

    results = await asyncio.gather(*(one(s) for s in symbols), return_exceptions=True)
    errors = [str(r) for r in results if isinstance(r, BaseException)]
    if errors:
        logger.warning("24h stats poll failed: %s", errors[0])


async def _ws_forever(
    session_factory: async_sessionmaker[AsyncSession], base: list[str]
) -> None:
    """Keep a live ticker WebSocket open, refreshing the subscription set."""
    backoff = 1.0
    while True:
        symbols = await _symbols(session_factory, base)
        if not symbols:
            await asyncio.sleep(10)
            continue
        try:
            logger.info("live ticker stream subscribing %s symbol(s)", len(symbols))
            consume = asyncio.create_task(_consume(symbols))
            refresh = asyncio.create_task(_refresh_symbols(session_factory, base, symbols))
            done, pending = await asyncio.wait(
                {consume, refresh}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            for task in done:
                exc = task.exception()
                if exc:
                    raise exc
            backoff = 1.0
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("live ticker stream lost (%s); reconnecting in %.0fs", exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 300.0)


async def _refresh_symbols(
    session_factory: async_sessionmaker[AsyncSession],
    base: list[str],
    current: set[str],
) -> None:
    while True:
        await asyncio.sleep(60)
        wanted = await _symbols(session_factory, base)
        if wanted != current:
            logger.info("live ticker subscription changed; reconnecting")
            return

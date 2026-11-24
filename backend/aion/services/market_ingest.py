"""Public market data ingestion (no API keys, read-only).

Everything written here comes from Binance's public USDⓈ-M endpoints. If a
request fails the affected fields stay empty — we never synthesise prices.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aion.models import DerivativesSnapshot, MarketCandle, MarketSnapshot, MarketSymbol

logger = logging.getLogger(__name__)
FAPI = "https://fapi.binance.com"
D = Decimal
INTERVALS = ("15m", "1h")


def _ms(value: Any) -> dt.datetime | None:
    if value in (None, ""):
        return None
    try:
        return dt.datetime.fromtimestamp(int(value) / 1000, tz=dt.UTC)
    except (TypeError, ValueError):
        return None


def _dec(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return D(str(value))
    except Exception:  # noqa: BLE001
        return None


async def _get(client: httpx.AsyncClient, path: str, params: dict[str, Any] | None = None) -> Any:
    response = await client.get(path, params=params)
    response.raise_for_status()
    return response.json()


async def sync_symbols(
    client: httpx.AsyncClient, db: AsyncSession, symbols: list[str]
) -> int:
    info = await _get(client, "/fapi/v1/exchangeInfo")
    wanted = {s.upper() for s in symbols}
    updated = 0
    for item in info.get("symbols", []):
        symbol = item.get("symbol", "")
        if symbol not in wanted or item.get("status") != "TRADING":
            continue
        filters = {f.get("filterType"): f for f in item.get("filters", [])}
        price_filter = filters.get("PRICE_FILTER", {})
        lot = filters.get("LOT_SIZE", {})
        min_notional = filters.get("MIN_NOTIONAL", {})
        values = {
            "symbol": symbol,
            "base_asset": item.get("baseAsset", ""),
            "quote_asset": item.get("quoteAsset", "USDT"),
            "contract_type": item.get("contractType", "PERPETUAL"),
            "status": item.get("status", "TRADING"),
            "price_precision": int(item.get("pricePrecision", 2)),
            "quantity_precision": int(item.get("quantityPrecision", 3)),
            "tick_size": _dec(price_filter.get("tickSize")) or D("0.01"),
            "step_size": _dec(lot.get("stepSize")) or D("0.001"),
            "min_qty": _dec(lot.get("minQty")) or D("0.001"),
            "max_qty": _dec(lot.get("maxQty")) or D("1000"),
            "min_notional": _dec(min_notional.get("notional")) or D("5"),
            "is_allowed": True,
            "filters_raw": filters,
            "rules_synced_at": dt.datetime.now(dt.UTC),
        }
        stmt = pg_insert(MarketSymbol).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[MarketSymbol.symbol],
            set_={k: v for k, v in values.items() if k != "symbol"},
        )
        await db.execute(stmt)
        updated += 1
    return updated


async def _ingest_symbol(
    client: httpx.AsyncClient, db: AsyncSession, symbol: str
) -> dict[str, int]:
    counts = {"candles": 0, "snapshots": 1, "derivatives": 0}

    ticker = await _get(client, "/fapi/v1/ticker/24hr", {"symbol": symbol})
    premium = await _get(client, "/fapi/v1/premiumIndex", {"symbol": symbol})
    try:
        oi = await _get(client, "/fapi/v1/openInterest", {"symbol": symbol})
    except httpx.HTTPError:
        oi = {}

    now = dt.datetime.now(dt.UTC)
    mark = _dec(premium.get("markPrice"))
    index = _dec(premium.get("indexPrice"))
    last = _dec(ticker.get("lastPrice")) or mark
    if last is None:
        raise ValueError(f"{symbol}: no price in public response")

    snapshot = {
        "symbol": symbol,
        "captured_at": now,
        "last_price": last,
        "mark_price": mark,
        "index_price": index,
        "funding_rate": _dec(premium.get("lastFundingRate")),
        "next_funding_at": _ms(premium.get("nextFundingTime")),
        "open_interest": _dec(oi.get("openInterest")),
        "volume_24h": _dec(ticker.get("quoteVolume")),
        "source_ts": _ms(ticker.get("closeTime")),
    }
    stmt = pg_insert(MarketSnapshot).values(**snapshot)
    # snapshots are append-only observations; no unique key to conflict on
    await db.execute(stmt)

    basis = None
    if mark is not None and index not in (None, D("0")):
        basis = (mark - index) / index * D("100")
    if any(v is not None for v in (snapshot["funding_rate"], snapshot["open_interest"], basis)):
        deriv = {
            "symbol": symbol,
            "captured_at": now,
            "funding_rate": snapshot["funding_rate"],
            "open_interest": snapshot["open_interest"],
            "basis_pct": basis,
            "data_available": {
                "funding_rate": snapshot["funding_rate"] is not None,
                "open_interest": snapshot["open_interest"] is not None,
                "basis": basis is not None,
            },
        }
        stmt = pg_insert(DerivativesSnapshot).values(**deriv)
        await db.execute(stmt)
        counts["derivatives"] = 1

    for interval in INTERVALS:
        rows = await _get(
            client, "/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": 200}
        )
        for idx, row in enumerate(rows):
            open_time = _ms(row[0])
            close_time = _ms(row[6])
            if open_time is None or close_time is None:
                continue
            # The last row is the candle still forming; store it as non-final.
            is_final = idx < len(rows) - 1
            values = {
                "symbol": symbol,
                "interval": interval,
                "open_time": open_time,
                "close_time": close_time,
                "open": _dec(row[1]) or D("0"),
                "high": _dec(row[2]) or D("0"),
                "low": _dec(row[3]) or D("0"),
                "close": _dec(row[4]) or D("0"),
                "volume": _dec(row[5]) or D("0"),
                "quote_volume": _dec(row[7]) or D("0"),
                "trades": int(row[8]) if row[8] is not None else 0,
                "is_final": is_final,
                "source": "binance",
            }
            stmt = pg_insert(MarketCandle).values(**values)
            stmt = stmt.on_conflict_do_update(
                index_elements=[
                    MarketCandle.symbol,
                    MarketCandle.interval,
                    MarketCandle.open_time,
                ],
                set_={k: v for k, v in values.items() if k not in ("symbol", "interval", "open_time")},
            )
            await db.execute(stmt)
            counts["candles"] += 1
    return counts


async def ingest_once(
    session_factory: async_sessionmaker[AsyncSession], symbols: list[str]
) -> dict[str, Any]:
    """One ingestion pass. Per-symbol failures are reported, not hidden."""
    started = dt.datetime.now(dt.UTC)
    stats: dict[str, Any] = {"symbols": {}, "errors": {}}
    async with httpx.AsyncClient(base_url=FAPI, timeout=httpx.Timeout(10.0)) as client:
        async with session_factory() as db:
            try:
                stats["symbols_synced"] = await sync_symbols(client, db, symbols)
            except httpx.HTTPError as exc:
                stats["errors"]["exchangeInfo"] = str(exc)[:200]
            await db.commit()

        for symbol in symbols:
            async with session_factory() as db:
                try:
                    stats["symbols"][symbol] = await _ingest_symbol(client, db, symbol)
                    await db.commit()
                except Exception as exc:  # noqa: BLE001 - one symbol must not stop the rest
                    await db.rollback()
                    stats["errors"][symbol] = str(exc)[:200]
                    logger.warning("market ingest failed for %s: %s", symbol, exc)

    stats["elapsed_ms"] = int((dt.datetime.now(dt.UTC) - started).total_seconds() * 1000)
    return stats


async def run_forever(
    session_factory: async_sessionmaker[AsyncSession],
    symbols: list[str],
    interval_seconds: int,
) -> None:
    logger.info("market ingest loop started (every %ss, symbols=%s)", interval_seconds, symbols)
    while True:
        try:
            stats = await ingest_once(session_factory, symbols)
            if stats.get("errors"):
                logger.warning("market ingest completed with errors: %s", stats["errors"])
        except asyncio.CancelledError:
            logger.info("market ingest loop stopped")
            raise
        except Exception:  # noqa: BLE001 - the loop must survive transient failures
            logger.exception("market ingest pass crashed")
        await asyncio.sleep(max(interval_seconds, 15))

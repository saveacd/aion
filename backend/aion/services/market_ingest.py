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
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aion.core.redis import distributed_lock
from aion.models import DerivativesSnapshot, MarketCandle, MarketSnapshot, MarketSymbol
from aion.models.market import MarketCandleRevision, MarketFundingSettlement
from aion.quant.datasets import INTERVAL_SECONDS

logger = logging.getLogger(__name__)
FAPI = "https://fapi.binance.com"
D = Decimal
INTERVALS = tuple(INTERVAL_SECONDS)
HISTORY_LIMIT = 1800
HISTORY_PAGE = 900
INCREMENTAL_LIMIT = 200


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
    server_time = _ms(ticker.get("closeTime"))
    if server_time is None or abs((server_time - now).total_seconds()) > 120:
        raise ValueError(f"{symbol}: ticker timestamp is outside the clock tolerance")
    mark = _dec(premium.get("markPrice"))
    index = _dec(premium.get("indexPrice"))
    last = _dec(ticker.get("lastPrice")) or mark
    if last is None or not last.is_finite() or last <= 0:
        raise ValueError(f"{symbol}: invalid price in public response")

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
    ratio = None
    try:
        ratios = await _get(client, "/futures/data/globalLongShortAccountRatio",
                            {"symbol": symbol, "period": "5m", "limit": 1})
        if ratios:
            ts = _ms(ratios[-1].get("timestamp"))
            if ts is not None and 0 <= (server_time - ts).total_seconds() <= 900:
                ratio = _dec(ratios[-1].get("longShortRatio"))
    except (httpx.HTTPError, TypeError, KeyError, ValueError, AttributeError):
        pass
    if any(v is not None for v in (snapshot["funding_rate"], snapshot["open_interest"], basis, ratio)):
        deriv = {
            "symbol": symbol,
            "captured_at": now,
            "funding_rate": snapshot["funding_rate"],
            "open_interest": snapshot["open_interest"],
            "basis_pct": basis,
            "long_short_ratio": ratio,
            "data_available": {
                "funding_rate": snapshot["funding_rate"] is not None,
                "open_interest": snapshot["open_interest"] is not None,
                "basis": basis is not None,
                "long_short_ratio": ratio is not None,
            },
        }
        stmt = pg_insert(DerivativesSnapshot).values(**deriv)
        await db.execute(stmt)
        counts["derivatives"] = 1

    counts["funding_settlements"] = await _ingest_funding(client, db, symbol, server_time)

    # Re-fetch a bounded history window so reconnects can repair missing rows.
    counts["invalid_candles"] = 0
    counts["revisions"] = 0
    for interval in INTERVALS:
        period = dt.timedelta(seconds=INTERVAL_SECONDS[interval])
        latest = await db.scalar(select(MarketCandle.close_time).where(
            MarketCandle.symbol == symbol, MarketCandle.interval == interval, MarketCandle.is_final.is_(True),
        ).order_by(MarketCandle.open_time.desc()).limit(1))
        if latest is not None and latest + period >= server_time:
            continue
        old_bar = await db.scalar(select(MarketCandle.open_time).where(
            MarketCandle.symbol == symbol, MarketCandle.interval == interval, MarketCandle.is_final.is_(True),
        ).order_by(MarketCandle.open_time.desc()).offset(HISTORY_LIMIT - INCREMENTAL_LIMIT).limit(1))
        bootstrap = old_bar is None and interval not in {"3d", "1w"}
        params = {"symbol": symbol, "interval": interval,
                  "limit": HISTORY_PAGE if bootstrap else INCREMENTAL_LIMIT}
        rows = await _get(client, "/fapi/v1/klines", params)
        if bootstrap and len(rows) == HISTORY_PAGE:
            older = await _get(client, "/fapi/v1/klines", {**params, "endTime": int(rows[0][0]) - 1})
            rows = older + rows
        counts_for_interval = await _upsert_candles(db, symbol, interval, rows, server_time)
        for key, value in counts_for_interval.items():
            counts[key] += value

    return counts


async def _ingest_funding(client, db, symbol, server_time) -> int:
    latest = await db.scalar(select(MarketFundingSettlement.settled_at).where(
        MarketFundingSettlement.symbol == symbol,
    ).order_by(MarketFundingSettlement.settled_at.desc()).limit(1))
    if latest is not None and server_time < latest + dt.timedelta(hours=1):
        return 0
    params = {"symbol": symbol, "limit": 1000}
    if latest is not None:
        params["startTime"] = int(latest.timestamp() * 1000) + 1
    try:
        raw = await _get(client, "/fapi/v1/fundingRate", params)
    except httpx.HTTPError:
        logger.warning("funding history unavailable for %s", symbol)
        return 0
    rows = {}
    for item in raw:
        timestamp = _ms(item.get("fundingTime"))
        rate = _dec(item.get("fundingRate"))
        if timestamp is not None and rate is not None and rate.is_finite() and timestamp <= server_time:
            rows[timestamp] = {"symbol": symbol, "settled_at": timestamp, "rate": rate}
    if rows:
        await db.execute(pg_insert(MarketFundingSettlement).on_conflict_do_nothing(), list(rows.values()))
    return len(rows)


async def _upsert_candles(db: AsyncSession, symbol: str, interval: str, rows, server_time) -> dict[str, int]:
    counts = {"candles": 0, "invalid_candles": 0, "revisions": 0}
    validated = {}
    duplicates = set()
    duration = dt.timedelta(seconds=INTERVAL_SECONDS[interval])
    for row in rows:
        try:
            start, end = _ms(row[0]), _ms(row[6])
            o, h, low, close, volume, quote = (_dec(row[i]) for i in (1, 2, 3, 4, 5, 7))
            trades = int(row[8])
            if (
                start is None or end is None or end - start != duration - dt.timedelta(milliseconds=1)
                or any(value is None or not value.is_finite() for value in (o, h, low, close, volume, quote))
                or low <= 0 or low > min(o, close) or h < max(o, close)
                or volume < 0 or quote < 0 or trades < 0 or start > server_time
            ):
                raise ValueError("invalid OHLCV row")
        except (ValueError, TypeError, IndexError, OverflowError):
            counts["invalid_candles"] += 1
            continue
        if start in validated:
            duplicates.add(start)
            counts["invalid_candles"] += 1
            continue
        validated[start] = dict(
            symbol=symbol, interval=interval, open_time=start, close_time=end,
            open=o, high=h, low=low, close=close, volume=volume, quote_volume=quote,
            trades=trades, is_final=end < server_time, source="binance",
        )
    for start in duplicates:
        validated.pop(start, None)
    if not validated:
        return counts

    # An exchange response can repair a gap or revise a finalized bar. A late
    # forming response may never replace a finalized bar.
    current = (await db.scalars(select(MarketCandle).where(
        MarketCandle.symbol == symbol, MarketCandle.interval == interval,
        MarketCandle.open_time.in_(tuple(validated)),
    ).with_for_update())).all()
    existing = {row.open_time: row for row in current}
    updates = []
    for start, values in validated.items():
        old = existing.get(start)
        changed = {key: value for key, value in values.items() if old is None or getattr(old, key) != value}
        if not changed or (old is not None and old.is_final and not values["is_final"]):
            continue
        if old is not None and old.is_final:
            db.add(MarketCandleRevision(
                symbol=symbol, interval=interval, open_time=start,
                previous={key: str(getattr(old, key)) for key in changed},
                replacement={key: str(value) for key, value in changed.items()},
            ))
            counts["revisions"] += 1
        updates.append(values)
    if updates:
        stmt = pg_insert(MarketCandle)
        await db.execute(stmt.on_conflict_do_update(
            index_elements=[MarketCandle.symbol, MarketCandle.interval, MarketCandle.open_time],
            set_={key: getattr(stmt.excluded, key) for key in updates[0] if key not in {"symbol", "interval", "open_time"}},
            where=(MarketCandle.is_final.is_(False) | stmt.excluded.is_final.is_(True)),
        ), updates)
    counts["candles"] = len(updates)
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
            async with distributed_lock("market-ingest", wait_seconds=0) as acquired:
                stats = await ingest_once(session_factory, symbols) if acquired else {}
            if stats.get("errors"):
                logger.warning("market ingest completed with errors: %s", stats["errors"])
        except asyncio.CancelledError:
            logger.info("market ingest loop stopped")
            raise
        except Exception:  # noqa: BLE001 - the loop must survive transient failures
            logger.exception("market ingest pass crashed")
        await asyncio.sleep(max(interval_seconds, 15))

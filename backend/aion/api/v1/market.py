"""Market data endpoints backed by persisted snapshots and candles."""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any

from fastapi import APIRouter, Path, Query
from pydantic import Field
from sqlalchemy import select

from aion.api.deps import CurrentUser, DbSession, SettingsDep
from aion.models.base import utcnow
from aion.models.market import (
    DerivativesSnapshot,
    MarketCandle,
    MarketSnapshot,
    MarketSymbol,
)
from aion.schemas.common import AionModel
from aion.schemas.market import (
    CandleOut,
    CandleSeries,
    DerivativesOut,
    SymbolInfo,
    TickerOut,
)
from aion.services.market_stream import fetch_live_klines, latest_price

router = APIRouter(prefix="/market", tags=["market"])

INTERVAL_PATTERN = r"^(1m|3m|5m|15m|30m|1h|2h|4h|6h|8h|12h|1d|3d|1w)$"
SYMBOL_PATTERN = r"^[A-Za-z0-9]{3,32}$"

INTERVAL_SECONDS: dict[str, int] = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "6h": 21600,
    "8h": 28800,
    "12h": 43200,
    "1d": 86400,
    "3d": 259200,
    "1w": 604800,
}


class SymbolList(AionModel):
    items: list[SymbolInfo] = Field(default_factory=list)
    total: int = 0


class TickerResponse(AionModel):
    has_data: bool
    symbol: str
    ticker: TickerOut | None = None


class DerivativesResponse(AionModel):
    has_data: bool
    symbol: str
    derivatives: DerivativesOut | None = None


def _normalize(symbol: str) -> str:
    return symbol.strip().upper()


def _is_stale(ts: dt.datetime | None, threshold_seconds: int) -> bool:
    if ts is None:
        return True
    return (utcnow() - ts).total_seconds() > threshold_seconds


@router.get("/symbols", response_model=SymbolList)
async def symbols(
    db: DbSession,
    user: CurrentUser,
    allowed_only: bool = True,
) -> SymbolList:
    stmt = select(MarketSymbol).order_by(MarketSymbol.symbol)
    if allowed_only:
        stmt = stmt.where(MarketSymbol.is_allowed.is_(True))
    rows = (await db.execute(stmt)).scalars().all()
    return SymbolList(items=[SymbolInfo.model_validate(row) for row in rows], total=len(rows))


@router.get("/candles", response_model=CandleSeries)
async def candles(
    db: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
    symbol: Annotated[str, Query(pattern=SYMBOL_PATTERN)],
    interval: Annotated[str, Query(pattern=INTERVAL_PATTERN)] = "15m",
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> CandleSeries:
    normalized = _normalize(symbol)
    stmt = (
        select(MarketCandle)
        .where(MarketCandle.symbol == normalized, MarketCandle.interval == interval)
        .order_by(MarketCandle.open_time.desc())
        .limit(limit)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    rows.reverse()

    out = [CandleOut.model_validate(row) for row in rows]
    merged_live = await _append_live_bar(out, normalized, interval, limit)
    return CandleSeries(
        symbol=normalized,
        interval=interval,
        candles=out,
        stale=False if merged_live else _candles_stale(out, interval, settings),
    )


async def _append_live_bar(
    out: list[CandleOut], symbol: str, interval: str, limit: int
) -> bool:
    """Append Binance's forming bar when it is newer than the stored history."""
    bars = await fetch_live_klines(symbol, interval=interval, limit=2, timeout_seconds=3.0)
    if not bars:
        return False
    last = bars[-1]
    try:
        open_time = dt.datetime.fromisoformat(last["t"])
    except (KeyError, TypeError, ValueError):
        return False
    if out and open_time <= out[-1].open_time:
        return False
    close_time = open_time + dt.timedelta(
        seconds=INTERVAL_SECONDS[interval]
    ) - dt.timedelta(milliseconds=1)
    out.append(
        CandleOut(
            open_time=open_time,
            close_time=close_time,
            open=last["o"],
            high=last["h"],
            low=last["l"],
            close=last["c"],
            volume=last["v"],
            quote_volume="0",
            is_final=False,
        )
    )
    del out[:-limit]
    return True


def _candles_stale(
    out: list[CandleOut], interval: str, settings: Any
) -> bool:
    """Staleness is relative to the bar close, not its open.

    Ingestion persists only finalized bars, so the newest bar can be one whole
    interval old even when the feed is healthy.
    """
    if not out:
        return True
    seconds = INTERVAL_SECONDS[interval]
    last_open = out[-1].open_time
    age = (utcnow() - last_open).total_seconds()
    return age > seconds + settings.market_stale_seconds


@router.get("/live")
async def live_prices(user: CurrentUser) -> dict[str, Any]:
    """In-memory 1-second ticker feed (Binance public WebSocket)."""
    from aion.services.market_stream import all_prices

    return {
        "server_time": dt.datetime.now(dt.UTC).isoformat(),
        "items": all_prices(),
    }


@router.get("/snapshot/{symbol}", response_model=TickerResponse)
async def snapshot(
    db: DbSession,
    settings: SettingsDep,
    user: CurrentUser,
    symbol: Annotated[str, Path(pattern=SYMBOL_PATTERN)],
) -> TickerResponse:
    normalized = _normalize(symbol)
    live = latest_price(normalized)
    stmt = (
        select(MarketSnapshot)
        .where(MarketSnapshot.symbol == normalized)
        .order_by(MarketSnapshot.captured_at.desc())
        .limit(1)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None and live is None:
        return TickerResponse(has_data=False, symbol=normalized, ticker=None)

    return TickerResponse(
        has_data=True,
        symbol=normalized,
        ticker=TickerOut(
            symbol=normalized,
            last_price=live["price"] if live else row.last_price,
            mark_price=row.mark_price if row else None,
            index_price=row.index_price if row else None,
            price_change_pct_24h=live["change_pct"] if live else None,
            volume_24h=row.volume_24h if row else None,
            source_ts=live["ts"] if live else (row.source_ts if row else None),
            stale=False if live else _is_stale(row.captured_at, settings.market_stale_seconds),
        ),
    )


@router.get("/derivatives/{symbol}", response_model=DerivativesResponse)
async def derivatives(
    db: DbSession,
    user: CurrentUser,
    symbol: Annotated[str, Path(pattern=SYMBOL_PATTERN)],
) -> DerivativesResponse:
    normalized = _normalize(symbol)
    stmt = (
        select(DerivativesSnapshot)
        .where(DerivativesSnapshot.symbol == normalized)
        .order_by(DerivativesSnapshot.captured_at.desc())
        .limit(1)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        return DerivativesResponse(has_data=False, symbol=normalized, derivatives=None)

    data_available: dict[str, Any] = {}
    for key, value in (row.data_available or {}).items():
        data_available[str(key)] = bool(value)

    return DerivativesResponse(
        has_data=True,
        symbol=normalized,
        derivatives=DerivativesOut(
            symbol=row.symbol,
            funding_rate=row.funding_rate,
            funding_rate_avg_8h=row.funding_rate_avg_8h,
            open_interest=row.open_interest,
            open_interest_change_pct=row.open_interest_change_pct,
            long_short_ratio=row.long_short_ratio,
            taker_buy_sell_ratio=row.taker_buy_sell_ratio,
            basis_pct=row.basis_pct,
            data_available=data_available,
            captured_at=row.captured_at,
        ),
    )

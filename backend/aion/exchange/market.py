"""Public market data endpoints (client mixin), parsed into Decimal dataclasses."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from aion.exchange.orders import to_decimal
from aion.exchange.symbols import SymbolRules, build_symbol_map

D = Decimal


@dataclass(frozen=True, slots=True)
class Kline:
    open_time: dt.datetime
    close_time: dt.datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal
    trades: int


@dataclass(frozen=True, slots=True)
class TickerPrice:
    symbol: str
    price: Decimal
    time_ms: int | None = None


@dataclass(frozen=True, slots=True)
class MarkPrice:
    symbol: str
    mark_price: Decimal
    index_price: Decimal
    funding_rate: Decimal
    next_funding_time: dt.datetime | None = None


@dataclass(frozen=True, slots=True)
class FundingRate:
    symbol: str
    funding_rate: Decimal
    funding_time: dt.datetime


@dataclass(frozen=True, slots=True)
class OpenInterest:
    symbol: str
    open_interest: Decimal
    time_ms: int | None = None


@dataclass(frozen=True, slots=True)
class DepthLevel:
    price: Decimal
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class OrderBook:
    symbol: str
    last_update_id: int
    bids: tuple[DepthLevel, ...]
    asks: tuple[DepthLevel, ...]
    event_time_ms: int | None = None


@dataclass(frozen=True, slots=True)
class ExchangeInfo:
    symbols: dict[str, SymbolRules]
    server_time_ms: int | None = None
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)


def _ms_to_dt(value: Any) -> dt.datetime | None:
    if value is None:
        return None
    return dt.datetime.fromtimestamp(int(value) / 1000, tz=dt.UTC)


class MarketAPI:
    """Mixin: public market data (no signing required)."""

    async def exchange_info(self) -> ExchangeInfo:
        payload = await self._public_request("GET", "/fapi/v1/exchangeInfo")  # type: ignore[attr-defined]
        return ExchangeInfo(
            symbols=build_symbol_map(payload),
            server_time_ms=payload.get("serverTime"),
            raw=payload,
        )

    async def klines(self, symbol: str, interval: str = "1m", limit: int = 500) -> list[Kline]:
        if not 1 <= limit <= 1500:
            raise ValueError("limit must be between 1 and 1500")
        payload = await self._public_request(  # type: ignore[attr-defined]
            "GET", "/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": int(limit)}
        )
        return [
            Kline(
                open_time=_ms_to_dt(row[0]) or dt.datetime.fromtimestamp(0, tz=dt.UTC),
                close_time=_ms_to_dt(row[6]) or dt.datetime.fromtimestamp(0, tz=dt.UTC),
                open=to_decimal(row[1]),
                high=to_decimal(row[2]),
                low=to_decimal(row[3]),
                close=to_decimal(row[4]),
                volume=to_decimal(row[5]),
                quote_volume=to_decimal(row[7]),
                trades=int(row[8]),
            )
            for row in payload
        ]

    async def ticker_price(self, symbol: str) -> TickerPrice:
        payload = await self._public_request(  # type: ignore[attr-defined]
            "GET", "/fapi/v1/ticker/price", {"symbol": symbol}
        )
        return TickerPrice(
            symbol=str(payload.get("symbol", symbol)),
            price=to_decimal(payload.get("price")),
            time_ms=payload.get("time"),
        )

    async def mark_price(self, symbol: str) -> MarkPrice:
        payload = await self._public_request(  # type: ignore[attr-defined]
            "GET", "/fapi/v1/premiumIndex", {"symbol": symbol}
        )
        return MarkPrice(
            symbol=str(payload.get("symbol", symbol)),
            mark_price=to_decimal(payload.get("markPrice")),
            index_price=to_decimal(payload.get("indexPrice")),
            funding_rate=to_decimal(payload.get("lastFundingRate")),
            next_funding_time=_ms_to_dt(payload.get("nextFundingTime")),
        )

    async def funding_rate(self, symbol: str, limit: int = 100) -> list[FundingRate]:
        payload = await self._public_request(  # type: ignore[attr-defined]
            "GET", "/fapi/v1/fundingRate", {"symbol": symbol, "limit": int(limit)}
        )
        return [
            FundingRate(
                symbol=str(item.get("symbol", symbol)),
                funding_rate=to_decimal(item.get("fundingRate")),
                funding_time=_ms_to_dt(item.get("fundingTime")) or dt.datetime.fromtimestamp(0, tz=dt.UTC),
            )
            for item in payload
        ]

    async def open_interest(self, symbol: str) -> OpenInterest:
        payload = await self._public_request(  # type: ignore[attr-defined]
            "GET", "/fapi/v1/openInterest", {"symbol": symbol}
        )
        return OpenInterest(
            symbol=str(payload.get("symbol", symbol)),
            open_interest=to_decimal(payload.get("openInterest")),
            time_ms=payload.get("time"),
        )

    async def depth(self, symbol: str, limit: int = 50) -> OrderBook:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        payload = await self._public_request(  # type: ignore[attr-defined]
            "GET", "/fapi/v1/depth", {"symbol": symbol, "limit": int(limit)}
        )
        return OrderBook(
            symbol=str(payload.get("symbol", symbol)),
            last_update_id=int(payload.get("lastUpdateId", 0)),
            bids=tuple(DepthLevel(to_decimal(p), to_decimal(q)) for p, q in payload.get("bids", [])),
            asks=tuple(DepthLevel(to_decimal(p), to_decimal(q)) for p, q in payload.get("asks", [])),
            event_time_ms=payload.get("T"),
        )

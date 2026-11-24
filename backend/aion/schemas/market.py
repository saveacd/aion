from __future__ import annotations

import datetime as dt

from pydantic import Field

from aion.schemas.common import AionModel, ORMModel, Price, Qty


class SymbolInfo(ORMModel):
    symbol: str
    base_asset: str
    quote_asset: str
    contract_type: str
    status: str
    price_precision: int
    quantity_precision: int
    tick_size: Price
    step_size: Qty
    min_qty: Qty
    max_qty: Qty
    min_notional: Price
    max_leverage: int
    is_allowed: bool


class CandleOut(ORMModel):
    open_time: dt.datetime
    close_time: dt.datetime
    open: Price
    high: Price
    low: Price
    close: Price
    volume: Qty
    quote_volume: Qty
    is_final: bool


class CandleSeries(AionModel):
    symbol: str
    interval: str
    candles: list[CandleOut]
    source: str = "binance"
    stale: bool = False


class TickerOut(AionModel):
    symbol: str
    last_price: Price
    mark_price: Price | None = None
    index_price: Price | None = None
    price_change_pct_24h: Price | None = None
    high_24h: Price | None = None
    low_24h: Price | None = None
    volume_24h: Qty | None = None
    quote_volume_24h: Qty | None = None
    spread_bps: Price | None = None
    source_ts: dt.datetime | None = None
    stale: bool = False


class OrderBookLevel(AionModel):
    price: Price
    quantity: Qty


class OrderBookOut(AionModel):
    symbol: str
    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]
    best_bid: Price | None = None
    best_ask: Price | None = None
    spread_bps: Price | None = None
    captured_at: dt.datetime


class DerivativesOut(AionModel):
    symbol: str
    funding_rate: Price | None = None
    funding_rate_avg_8h: Price | None = None
    next_funding_at: dt.datetime | None = None
    open_interest: Qty | None = None
    open_interest_change_pct: Price | None = None
    long_short_ratio: Price | None = None
    taker_buy_sell_ratio: Price | None = None
    basis_pct: Price | None = None
    data_available: dict[str, bool] = Field(default_factory=dict)
    captured_at: dt.datetime | None = None


class MarketOverview(AionModel):
    symbol: str
    ticker: TickerOut | None = None
    derivatives: DerivativesOut | None = None

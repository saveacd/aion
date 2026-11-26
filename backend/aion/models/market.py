from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from aion.models.base import Base, CreatedAt, Timestamped, UUIDPrimaryKey, utcnow


class MarketSymbol(Timestamped, Base):
    __tablename__ = "market_symbols"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    base_asset: Mapped[str] = mapped_column(String(16), nullable=False)
    quote_asset: Mapped[str] = mapped_column(String(16), nullable=False)
    contract_type: Mapped[str] = mapped_column(String(24), nullable=False, default="PERPETUAL")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="TRADING")
    price_precision: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    quantity_precision: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    tick_size: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    step_size: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    min_qty: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    max_qty: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    min_notional: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    max_leverage: Mapped[int] = mapped_column(Integer, nullable=False, default=125)
    is_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    filters_raw: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    rules_synced_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class MarketCandle(CreatedAt, Base):
    __tablename__ = "market_candles"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    interval: Mapped[str] = mapped_column(String(8), primary_key=True)
    open_time: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    close_time: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    open: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    volume: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    quote_volume: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    trades: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_final: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="binance")


class MarketSnapshot(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "market_snapshots"
    __table_args__ = (Index("ix_market_snapshots_symbol_ts", "symbol", "captured_at"),)

    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    captured_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    last_price: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    mark_price: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    index_price: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    funding_rate: Mapped[Decimal | None] = mapped_column(Numeric(38, 11))
    next_funding_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    open_interest: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    volume_24h: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    source_ts: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class DerivativesSnapshot(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "derivatives_snapshots"
    __table_args__ = (Index("ix_derivatives_symbol_ts", "symbol", "captured_at"),)

    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    captured_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    funding_rate: Mapped[Decimal | None] = mapped_column(Numeric(38, 11))
    funding_rate_avg_8h: Mapped[Decimal | None] = mapped_column(Numeric(38, 11))
    open_interest: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    open_interest_change_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    long_short_ratio: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    taker_buy_sell_ratio: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    basis_pct: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    data_available: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)


class MarketCandleRevision(UUIDPrimaryKey, CreatedAt, Base):
    """Previous and replacement versions of a finalized public candle."""

    __tablename__ = "market_candle_revisions"
    __table_args__ = (Index("ix_market_candle_revisions_lookup", "symbol", "interval", "open_time"),)

    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    interval: Mapped[str] = mapped_column(String(8), nullable=False)
    open_time: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    previous: Mapped[dict[str, Any]] = mapped_column(nullable=False)
    replacement: Mapped[dict[str, Any]] = mapped_column(nullable=False)


class MarketFundingSettlement(CreatedAt, Base):
    """Actual published USD-M funding settlement, distinct from ticker estimates."""

    __tablename__ = "market_funding_settlements"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    settled_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    rate: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)

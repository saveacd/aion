from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from aion.models.base import (
    Base,
    CreatedAt,
    OwnedMixin,
    Timestamped,
    UUIDPrimaryKey,
    utcnow,
)

STRATEGY_STATES = (
    "RESEARCH",
    "BACKTEST",
    "PAPER",
    "SHADOW",
    "APPROVED",
    "ACTIVE",
    "SUSPENDED",
    "RETIRED",
)


class Strategy(OwnedMixin, UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "strategies"

    name: Mapped[str] = mapped_column(String(96), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="RESEARCH")
    symbols: Mapped[list[Any]] = mapped_column(nullable=False, default=list)
    interval: Mapped[str] = mapped_column(String(8), nullable=False, default="15m")
    active_version_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "strategy_versions.id", ondelete="SET NULL", use_alter=True, name="fk_strategy_active_version"
        ),
    )
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="user")
    notes: Mapped[str | None] = mapped_column(Text)


class StrategyVersion(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "strategy_versions"
    __table_args__ = (
        UniqueConstraint("strategy_id", "version", name="uq_strategy_versions_strategy_version"),
    )

    strategy_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    risk_assumptions: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    invalidation: Mapped[str | None] = mapped_column(Text)
    change_note: Mapped[str | None] = mapped_column(Text)
    code_ref: Mapped[str | None] = mapped_column(String(128))
    is_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    approved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(String(64))


class BacktestRun(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "backtest_runs"

    strategy_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("strategy_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    interval: Mapped[str] = mapped_column(String(8), nullable=False)
    start_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sample: Mapped[str] = mapped_column(String(16), nullable=False, default="in_sample")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="RUNNING")
    fee_bps: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False, default=4)
    slippage_bps: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False, default=2)
    metrics: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    equity_curve: Mapped[list[Any]] = mapped_column(nullable=False, default=list)
    trades_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class TradeIntent(OwnedMixin, UUIDPrimaryKey, CreatedAt, Base):
    """Structured intent produced by the AI; a request, never an authorization."""

    __tablename__ = "trade_intents"
    __table_args__ = (
        UniqueConstraint("intent_key", name="uq_trade_intents_intent_key"),
        Index("ix_trade_intents_symbol_created", "symbol", "created_at"),
    )

    # Idempotency key: same underlying decision must not create two orders.
    intent_key: Mapped[str] = mapped_column(String(128), nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="SET NULL")
    )
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    strategy_version: Mapped[int | None] = mapped_column(Integer)

    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)  # BUY | SELL
    position_side: Mapped[str] = mapped_column(String(8), nullable=False)  # LONG | SHORT
    intent_type: Mapped[str] = mapped_column(String(16), nullable=False, default="ENTRY")
    order_type: Mapped[str] = mapped_column(String(16), nullable=False, default="MARKET")
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    notional_usd: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    requested_leverage: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    entry_price: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    stop_loss: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    take_profit: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    time_in_force: Mapped[str] = mapped_column(String(8), nullable=False, default="GTC")
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    reason: Mapped[str | None] = mapped_column(Text)
    evidence_refs: Mapped[list[Any]] = mapped_column(nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING")
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="PAPER")
    rejection_code: Mapped[str | None] = mapped_column(String(64))
    rejection_detail: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)


class RiskPolicy(OwnedMixin, Timestamped, Base):
    """User-owned limits. The AI has no write path to this table."""

    __tablename__ = "risk_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    max_leverage: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    max_position_notional_usd: Mapped[Decimal] = mapped_column(
        Numeric(38, 12), nullable=False, default=1000
    )
    max_total_exposure_usd: Mapped[Decimal] = mapped_column(
        Numeric(38, 12), nullable=False, default=3000
    )
    max_single_trade_loss_usd: Mapped[Decimal] = mapped_column(
        Numeric(38, 12), nullable=False, default=25
    )
    daily_loss_limit_usd: Mapped[Decimal] = mapped_column(
        Numeric(38, 12), nullable=False, default=100
    )
    max_drawdown_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False, default=15)
    max_consecutive_losses: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    max_open_positions: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    max_directional_concentration_pct: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False, default=70
    )
    min_margin_ratio: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False, default=0.15)
    required_stop_loss: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    max_spread_bps: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False, default=15)
    min_confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False, default=0.6)

    # Hard bounds enforced server-side regardless of what the client sends.
    ABSOLUTE_MAX_LEVERAGE: int = 20
    ABSOLUTE_MAX_NOTIONAL: Decimal = Decimal("100000")
    ABSOLUTE_DAILY_LOSS: Decimal = Decimal("10000")


class RiskDecision(OwnedMixin, UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "risk_decisions"
    __table_args__ = (Index("ix_risk_decisions_symbol_created", "symbol", "created_at"),)

    intent_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("trade_intents.id", ondelete="SET NULL"), index=True
    )
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason_codes: Mapped[list[Any]] = mapped_column(nullable=False, default=list)
    checked_limits: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    risk_snapshot: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    decided_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    latency_ms: Mapped[int | None] = mapped_column(Integer)


class Order(OwnedMixin, UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("client_order_id", name="uq_orders_client_order_id"),
        Index("ix_orders_symbol_created", "symbol", "created_at"),
        Index("ix_orders_exchange_id", "exchange_order_id"),
    )

    client_order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    exchange_order_id: Mapped[str | None] = mapped_column(String(64))
    intent_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("trade_intents.id", ondelete="SET NULL"), index=True
    )
    risk_decision_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    position_side: Mapped[str] = mapped_column(String(8), nullable=False, default="LONG")
    order_type: Mapped[str] = mapped_column(String(16), nullable=False)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False, default="ENTRY")
    reduce_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    price: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    stop_price: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    trigger_direction: Mapped[str | None] = mapped_column(String(24))

    filled_quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False, default=0)
    avg_fill_price: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    last_fill_quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False, default=0)

    status: Mapped[str] = mapped_column(String(24), nullable=False, default="CREATED")
    time_in_force: Mapped[str] = mapped_column(String(8), nullable=False, default="GTC")
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="PAPER")
    submit_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_exchange_response: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    terminal_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class OrderEvent(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "order_events"
    __table_args__ = (Index("ix_order_events_order_created", "order_id", "created_at"),)

    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(24))
    to_status: Mapped[str | None] = mapped_column(String(24))
    payload: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    exchange_event_time: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class Fill(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "fills"
    __table_args__ = (UniqueConstraint("exchange_trade_id", name="uq_fills_exchange_trade_id"),)

    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    exchange_trade_id: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    quote_quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    commission: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False, default=0)
    commission_asset: Mapped[str | None] = mapped_column(String(16))
    realized_pnl: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    is_maker: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    traded_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

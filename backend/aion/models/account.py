from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
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


class AccountSnapshot(OwnedMixin, UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "account_snapshots"
    __table_args__ = (Index("ix_account_snapshots_captured", "captured_at"),)

    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    captured_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    total_wallet_balance: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False, default=0)
    total_unrealized_pnl: Mapped[Decimal] = mapped_column(
        Numeric(38, 12), nullable=False, default=0
    )
    total_margin_balance: Mapped[Decimal] = mapped_column(
        Numeric(38, 12), nullable=False, default=0
    )
    available_balance: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False, default=0)
    total_initial_margin: Mapped[Decimal] = mapped_column(
        Numeric(38, 12), nullable=False, default=0
    )
    total_maint_margin: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False, default=0)
    margin_ratio: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    equity_peak_usd: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    drawdown_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    daily_realized_pnl: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False, default=0)
    raw: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)


class PositionSnapshot(OwnedMixin, UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "positions_snapshots"
    __table_args__ = (Index("ix_positions_snapshots_symbol_ts", "symbol", "captured_at"),)

    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    captured_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    position_side: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    mark_price: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False, default=0)
    liquidation_price: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    isolated_margin: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    leverage: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    margin_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="CROSSED")
    has_stop_loss: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_take_profit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    stop_loss_price: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    take_profit_price: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    protection_verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class ReconciliationRun(OwnedMixin, UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "reconciliation_runs"

    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="RUNNING")
    started_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    differences: Mapped[list[Any]] = mapped_column(nullable=False, default=list)
    blocking: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str | None] = mapped_column(Text)


class RiskEvent(OwnedMixin, UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "risk_events"
    __table_args__ = (Index("ix_risk_events_created", "created_at"),)

    severity: Mapped[str] = mapped_column(String(16), nullable=False)  # INFO|WARN|CRITICAL
    code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(32))
    context: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    acknowledged_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class SystemEvent(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "system_events"
    __table_args__ = (Index("ix_system_events_created", "created_at"),)

    category: Mapped[str] = mapped_column(String(32), nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False, default="INFO")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)


class AuditLog(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_actor_created", "actor", "created_at"),)

    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    target: Mapped[str | None] = mapped_column(String(128))
    before: Mapped[dict[str, Any] | None] = mapped_column()
    after: Mapped[dict[str, Any] | None] = mapped_column()
    ip_address: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(64))
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class TradingControlState(Timestamped, Base):
    """Singleton row holding the emergency-control switches."""

    __tablename__ = "trading_control_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    stop_new_trades: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pause_ai: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    emergency_stop: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    emergency_reason: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[str | None] = mapped_column(String(64))
    engaged_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class Notification(UUIDPrimaryKey, CreatedAt, Base):
    """站内通知：成交通知、风控告警、系统事件。

    ``user_id`` 为空表示面向全部用户的系统公告。
    """

    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_user_created", "user_id", "created_at"),)

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="system")
    level: Mapped[str] = mapped_column(String(16), nullable=False, default="INFO")
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    read_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class IdempotencyKey(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("scope", "key", name="uq_idempotency_scope_key"),)

    scope: Mapped[str] = mapped_column(String(48), nullable=False)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

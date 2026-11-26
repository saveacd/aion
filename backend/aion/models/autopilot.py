"""Autopilot (全自动 AI 代理) and self-learning tables.

* ``AutopilotConfig`` — per-user授权配置：自动分析、自动模拟开仓开关与限额。
* ``PaperPosition`` — 模拟持仓，止盈止损由后端监控触发。
* ``TradeOutcome`` — 每一笔已平仓交易的结果，是学习进化的原始素材。
* ``LearningLesson`` — 服务器从交易结果中提炼的经验，注入后续分析输入。
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
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


class AutopilotConfig(Timestamped, Base):
    """One row per user. Nothing runs unless ``enabled`` is true."""

    __tablename__ = "autopilot_configs"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # 自动分析：按计划周期运行 AI 分析。
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 自动开仓：风控通过后自动成交。必须在 API 层二次确认。
    auto_execute: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 执行账户：PAPER / TESTNET / MAINNET。真实模式必须由用户显式选择。
    execution_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="PAPER")
    live_authorized_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    symbols: Mapped[list[Any]] = mapped_column(nullable=False, default=list)
    # AI 等待区：价格进入区间时立即重新分析（日内抓点）。
    watch_plans: Mapped[list[Any]] = mapped_column(nullable=False, default=list)
    interval: Mapped[str] = mapped_column(String(8), nullable=False, default="15m")
    # 日内模式：只做当天可平仓的机会，持仓超过 max_holding_minutes 自动平仓。
    intraday: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    max_holding_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=240)
    cycle_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=600)
    max_trades_per_day: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    min_confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False, default=Decimal("0.65"))
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("strategies.id", ondelete="SET NULL")
    )
    last_run_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    trades_today: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trades_today_date: Mapped[dt.date | None] = mapped_column(Date)


class PaperPosition(OwnedMixin, UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "paper_positions"
    __table_args__ = (Index("ix_paper_positions_user_status", "user_id", "status"),)

    intent_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("trade_intents.id", ondelete="SET NULL")
    )
    entry_order_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orders.id", ondelete="SET NULL")
    )
    exit_order_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orders.id", ondelete="SET NULL")
    )
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    position_side: Mapped[str] = mapped_column(String(8), nullable=False)  # LONG | SHORT
    quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    stop_loss: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    take_profit: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    leverage: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    margin_usd: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OPEN")
    exit_reason: Mapped[str | None] = mapped_column(String(16))  # TP | SL | TIMEOUT | MANUAL
    opened_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    closed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class LivePosition(OwnedMixin, UUIDPrimaryKey, CreatedAt, Base):
    """真实交易所持仓（TESTNET/MAINNET）。

    与 ``PaperPosition`` 分开存放，避免把模拟成交和真实成交混在一起。
    ``sl_order_id`` / ``tp_order_id`` 记录交易所侧保护单，服务端每轮巡检，
    缺失时会补挂。
    """

    __tablename__ = "live_positions"
    __table_args__ = (Index("ix_live_positions_user_status", "user_id", "status"),)

    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    intent_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("trade_intents.id", ondelete="SET NULL")
    )
    entry_order_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orders.id", ondelete="SET NULL")
    )
    exit_order_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orders.id", ondelete="SET NULL")
    )
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    position_side: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    stop_loss: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    take_profit: Mapped[Decimal | None] = mapped_column(Numeric(38, 12))
    leverage: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    margin_usd: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OPEN")
    protection_status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    sl_order_id: Mapped[str | None] = mapped_column(String(64))
    tp_order_id: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(Text)
    exit_reason: Mapped[str | None] = mapped_column(String(16))
    opened_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    closed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class TradeOutcome(OwnedMixin, UUIDPrimaryKey, CreatedAt, Base):
    """One closed trade, written by the paper broker. The learning input."""

    __tablename__ = "trade_outcomes"
    __table_args__ = (Index("ix_trade_outcomes_user_closed", "user_id", "closed_at"),)

    intent_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("trade_intents.id", ondelete="SET NULL")
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    position_side: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    exit_price: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    pnl_usd: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    fee_usd: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False, default=0)
    r_multiple: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    holding_minutes: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    regime: Mapped[str | None] = mapped_column(String(32))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    exit_reason: Mapped[str] = mapped_column(String(16), nullable=False)
    opened_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LearningLesson(CreatedAt, Base):
    """A distilled lesson. Injected into future analysis while ``active``."""

    __tablename__ = "learning_lessons"
    __table_args__ = (Index("ix_learning_lessons_user_active", "user_id", "active"),)

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    lesson: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False, default="general")
    tags: Mapped[list[Any]] = mapped_column(nullable=False, default=list)
    score: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False, default=0)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="llm_distill")
    outcomes_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

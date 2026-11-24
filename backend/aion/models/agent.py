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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aion.models.base import (
    Base,
    CreatedAt,
    OwnedMixin,
    Timestamped,
    UUIDPrimaryKey,
    utcnow,
)


class AgentRun(OwnedMixin, UUIDPrimaryKey, Timestamped, Base):
    """One orchestrated analysis cycle across the six agents."""

    __tablename__ = "agent_runs"
    __table_args__ = (Index("ix_agent_runs_symbol_created", "symbol", "created_at"),)

    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    interval: Mapped[str] = mapped_column(String(8), nullable=False, default="15m")
    trigger: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="RUNNING")
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="PAPER")

    started_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    model_name: Mapped[str | None] = mapped_column(String(64))
    provider: Mapped[str | None] = mapped_column(String(32))
    total_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_cost_usd: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False, default=0)

    verdict: Mapped[str | None] = mapped_column(String(24))  # TRADE | NO_TRADE | BLOCKED
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    summary: Mapped[str | None] = mapped_column(Text)
    data_quality: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    intent_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    messages: Mapped[list[AgentMessage]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="AgentMessage.seq"
    )


class AgentMessage(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "agent_messages"
    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_agent_messages_run_seq"),)

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    agent_name: Mapped[str] = mapped_column(String(48), nullable=False)
    role: Mapped[str] = mapped_column(String(24), nullable=False, default="analysis")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OK")
    summary: Mapped[str | None] = mapped_column(Text)
    structured: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    evidence: Mapped[list[Any]] = mapped_column(nullable=False, default=list)
    warnings: Mapped[list[Any]] = mapped_column(nullable=False, default=list)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    input_refs: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    model_call_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))

    run: Mapped[AgentRun] = relationship(back_populates="messages")


class ModelCall(OwnedMixin, UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "model_calls"
    __table_args__ = (Index("ix_model_calls_created_day", "created_at"),)

    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    purpose: Mapped[str] = mapped_column(String(48), nullable=False, default="agent")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OK")
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    request_meta: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)

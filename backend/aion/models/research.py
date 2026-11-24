"""Persistent research-lab workflow records for AION v2.12.

Research records are user-scoped and deliberately have no exchange-order relationship.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from aion.models.base import Base, CreatedAt, OwnedMixin, UUIDPrimaryKey, utcnow


class FactorResearchTask(OwnedMixin, UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "factor_research_tasks"
    __table_args__ = (
        Index("ix_factor_research_tasks_status_created", "status", "created_at"),
        UniqueConstraint("user_id", "idempotency_key", name="uq_factor_research_task_idempotency"),
    )
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    interval: Mapped[str] = mapped_column(String(8), nullable=False)
    horizon_bars: Mapped[int] = mapped_column(Integer, nullable=False, default=8)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="QUEUED")
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))


class FactorValidationResult(OwnedMixin, UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "factor_validation_results"
    factor_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("factor_records.id", ondelete="CASCADE"), nullable=False, index=True)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    passed: Mapped[bool] = mapped_column(nullable=False, default=False)
    sample_definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    warnings: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)


class FactorVersion(OwnedMixin, UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "factor_versions"
    factor_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("factor_records.id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    reason: Mapped[str] = mapped_column(String(500), nullable=False, default="research")


class FactorLifecycleEvent(OwnedMixin, UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "factor_lifecycle_events"
    factor_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("factor_records.id", ondelete="CASCADE"), nullable=False, index=True)
    from_status: Mapped[str | None] = mapped_column(String(16))
    to_status: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)


class ResearchTaskLog(OwnedMixin, UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "research_task_logs"
    task_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("factor_research_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    level: Mapped[str] = mapped_column(String(12), nullable=False, default="INFO")
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class FactorEnsembleVersion(OwnedMixin, UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "factor_ensemble_versions"
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    interval: Mapped[str] = mapped_column(String(8), nullable=False)
    regime: Mapped[str] = mapped_column(String(24), nullable=False)
    weights: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    diagnostics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class StrategyCandidate(OwnedMixin, UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "strategy_candidates"
    name: Mapped[str] = mapped_column(String(96), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PROPOSED")
    specification: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    source_research_task_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("factor_research_tasks.id", ondelete="SET NULL"))
    evaluation: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class PaperStrategySession(OwnedMixin, UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "paper_strategy_sessions"
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("strategy_candidates.id", ondelete="SET NULL"))
    strategy_version_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("strategy_versions.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(96), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OBSERVING")
    initial_cash: Mapped[object] = mapped_column(Numeric(24, 8), nullable=False)
    current_cash: Mapped[object] = mapped_column(Numeric(24, 8), nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class PaperTradeRecord(OwnedMixin, UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "paper_trade_records"
    session_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("paper_strategy_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity: Mapped[object] = mapped_column(Numeric(24, 8), nullable=False)
    entry_price: Mapped[object] = mapped_column(Numeric(24, 8), nullable=False)
    exit_price: Mapped[object | None] = mapped_column(Numeric(24, 8))
    fees: Mapped[object] = mapped_column(Numeric(24, 8), nullable=False, default=0)
    pnl: Mapped[object | None] = mapped_column(Numeric(24, 8))
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="OPEN")
    opened_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    closed_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))


class StrategyApproval(OwnedMixin, UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "strategy_approvals"
    strategy_version_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("strategy_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reviewer: Mapped[str] = mapped_column(String(96), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    risk_check: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class StrategyBacktest(OwnedMixin, UUIDPrimaryKey, CreatedAt, Base):
    """Immutable candidate backtest sample results; separate from deployable versions."""
    __tablename__ = "strategy_backtests"
    __table_args__ = (Index("ix_strategy_backtests_candidate_created", "candidate_id", "created_at"),)
    candidate_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("strategy_candidates.id", ondelete="CASCADE"), nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    interval: Mapped[str] = mapped_column(String(8), nullable=False)
    sample: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    assumptions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))

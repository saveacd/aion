"""Factor library: accepted factor research results, persisted per user."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import Index, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from aion.models.base import Base, CreatedAt, OwnedMixin, UUIDPrimaryKey


class FactorRecord(OwnedMixin, UUIDPrimaryKey, CreatedAt, Base):
    """One factor that passed (or was archived from) the deterministic referee."""

    __tablename__ = "factor_records"
    __table_args__ = (
        UniqueConstraint("user_id", "symbol", "interval", "factor_name", name="uq_factor_identity"),
        Index("ix_factor_records_discovery_status", "user_id", "symbol", "interval", "status",
              postgresql_where=text("expression IS NOT NULL")),
    )

    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    interval: Mapped[str] = mapped_column(String(8), nullable=False)
    factor_name: Mapped[str] = mapped_column(String(64), nullable=False)
    domain: Mapped[str] = mapped_column(String(32), nullable=False)
    agent: Mapped[str] = mapped_column(String(64), nullable=False)
    formula: Mapped[str] = mapped_column(String(48), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    lookback_bars: Mapped[int] = mapped_column(Integer, nullable=False)
    horizon_bars: Mapped[int] = mapped_column(Integer, nullable=False)
    ic_mean: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    ic_ir: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    hit_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    coverage: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    hypothesis: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    expression: Mapped[list[Any] | None] = mapped_column(nullable=True)
    discovery_state: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
    run_id_text: Mapped[str] = mapped_column(String(64), nullable=False, default="")


__all__ = ["FactorRecord"]

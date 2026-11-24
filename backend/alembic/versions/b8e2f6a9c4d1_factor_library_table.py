"""factor library table

Revision ID: b8e2f6a9c4d1
Revises: f4c9a17d2e60
Create Date: 2026-11-24 02:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b8e2f6a9c4d1"
down_revision: str | None = "b3e5d8f1a2c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "factor_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
        ),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("interval", sa.String(8), nullable=False),
        sa.Column("factor_name", sa.String(64), nullable=False),
        sa.Column("domain", sa.String(32), nullable=False),
        sa.Column("agent", sa.String(64), nullable=False),
        sa.Column("formula", sa.String(48), nullable=False),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("lookback_bars", sa.Integer(), nullable=False),
        sa.Column("horizon_bars", sa.Integer(), nullable=False),
        sa.Column("ic_mean", sa.Numeric(10, 6)),
        sa.Column("ic_ir", sa.Numeric(10, 6)),
        sa.Column("hit_rate", sa.Numeric(6, 4)),
        sa.Column("coverage", sa.Numeric(6, 4), nullable=False),
        sa.Column("hypothesis", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("run_id_text", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "user_id", "symbol", "interval", "factor_name", name="uq_factor_identity"
        ),
    )


def downgrade() -> None:
    op.drop_table("factor_records")

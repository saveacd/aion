"""live positions, notifications, autopilot execution mode

Revision ID: f4c9a17d2e60
Revises: e3a8f1c05b62
Create Date: 2026-11-20 18:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f4c9a17d2e60"
down_revision: str | None = "e3a8f1c05b62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "autopilot_configs",
        sa.Column("execution_mode", sa.String(16), nullable=False, server_default="PAPER"),
    )

    op.create_table(
        "live_positions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
        ),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column(
            "intent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("trade_intents.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "entry_order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "exit_order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="SET NULL"),
        ),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("position_side", sa.String(8), nullable=False),
        sa.Column("quantity", sa.Numeric(38, 12), nullable=False),
        sa.Column("entry_price", sa.Numeric(38, 12), nullable=False),
        sa.Column("stop_loss", sa.Numeric(38, 12)),
        sa.Column("take_profit", sa.Numeric(38, 12)),
        sa.Column("leverage", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("margin_usd", sa.Numeric(38, 12), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="OPEN"),
        sa.Column("protection_status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("sl_order_id", sa.String(64)),
        sa.Column("tp_order_id", sa.String(64)),
        sa.Column("last_error", sa.Text()),
        sa.Column("exit_reason", sa.String(16)),
        sa.Column(
            "opened_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_live_positions_user_id", "live_positions", ["user_id"])
    op.create_index("ix_live_positions_user_status", "live_positions", ["user_id", "status"])

    op.create_table(
        "notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
        ),
        sa.Column("kind", sa.String(32), nullable=False, server_default="system"),
        sa.Column("level", sa.String(16), nullable=False, server_default="INFO"),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("body", sa.Text()),
        sa.Column("data", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])
    op.create_index(
        "ix_notifications_user_created", "notifications", ["user_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_table("notifications")
    op.drop_table("live_positions")
    op.drop_column("autopilot_configs", "execution_mode")

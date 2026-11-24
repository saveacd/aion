"""autopilot configs, paper positions, trade outcomes, learning lessons

Revision ID: e3a8f1c05b62
Revises: d1b7c4e9a250
Create Date: 2026-11-20 14:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "e3a8f1c05b62"
down_revision: str | None = "d1b7c4e9a250"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "autopilot_configs",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("auto_execute", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("symbols", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("interval", sa.String(8), nullable=False, server_default="15m"),
        sa.Column("cycle_seconds", sa.Integer(), nullable=False, server_default="600"),
        sa.Column("max_trades_per_day", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("min_confidence", sa.Numeric(5, 4), nullable=False, server_default="0.65"),
        sa.Column(
            "strategy_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("strategies.id", ondelete="SET NULL"),
        ),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("trades_today", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("trades_today_date", sa.Date()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    op.create_table(
        "paper_positions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("intent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("trade_intents.id", ondelete="SET NULL")),
        sa.Column("entry_order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orders.id", ondelete="SET NULL")),
        sa.Column("exit_order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orders.id", ondelete="SET NULL")),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("position_side", sa.String(8), nullable=False),
        sa.Column("quantity", sa.Numeric(38, 12), nullable=False),
        sa.Column("entry_price", sa.Numeric(38, 12), nullable=False),
        sa.Column("stop_loss", sa.Numeric(38, 12)),
        sa.Column("take_profit", sa.Numeric(38, 12)),
        sa.Column("leverage", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("margin_usd", sa.Numeric(38, 12), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="OPEN"),
        sa.Column("exit_reason", sa.String(16)),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_paper_positions_user_id", "paper_positions", ["user_id"])
    op.create_index("ix_paper_positions_user_status", "paper_positions", ["user_id", "status"])

    op.create_table(
        "trade_outcomes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("intent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("trade_intents.id", ondelete="SET NULL")),
        sa.Column("run_id", postgresql.UUID(as_uuid=True)),
        sa.Column("strategy_id", postgresql.UUID(as_uuid=True)),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("position_side", sa.String(8), nullable=False),
        sa.Column("quantity", sa.Numeric(38, 12), nullable=False),
        sa.Column("entry_price", sa.Numeric(38, 12), nullable=False),
        sa.Column("exit_price", sa.Numeric(38, 12), nullable=False),
        sa.Column("pnl_usd", sa.Numeric(38, 12), nullable=False),
        sa.Column("fee_usd", sa.Numeric(38, 12), nullable=False, server_default="0"),
        sa.Column("r_multiple", sa.Numeric(20, 8)),
        sa.Column("holding_minutes", sa.Numeric(20, 4)),
        sa.Column("regime", sa.String(32)),
        sa.Column("confidence", sa.Numeric(5, 4)),
        sa.Column("exit_reason", sa.String(16), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_trade_outcomes_user_id", "trade_outcomes", ["user_id"])
    op.create_index("ix_trade_outcomes_user_closed", "trade_outcomes", ["user_id", "closed_at"])

    op.create_table(
        "learning_lessons",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("lesson", sa.Text(), nullable=False),
        sa.Column("category", sa.String(32), nullable=False, server_default="general"),
        sa.Column("tags", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("score", sa.Numeric(10, 4), nullable=False, server_default="0"),
        sa.Column("source", sa.String(32), nullable=False, server_default="llm_distill"),
        sa.Column("outcomes_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_learning_lessons_user_active", "learning_lessons", ["user_id", "active"])


def downgrade() -> None:
    op.drop_table("learning_lessons")
    op.drop_table("trade_outcomes")
    op.drop_table("paper_positions")
    op.drop_table("autopilot_configs")

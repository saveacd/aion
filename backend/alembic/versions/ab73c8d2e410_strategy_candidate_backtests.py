"""Persist sample-separated strategy candidate backtests.

Revision ID: ab73c8d2e410
Revises: 9c12a6f4b801
Create Date: 2026-11-24
"""
from __future__ import annotations
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "ab73c8d2e410"
down_revision: str | None = "9c12a6f4b801"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    uid = postgresql.UUID(as_uuid=True)
    js = postgresql.JSONB(astext_type=sa.Text())
    op.create_table(
        "strategy_backtests",
        sa.Column("id", uid, primary_key=True),
        sa.Column("user_id", uid, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("candidate_id", uid, sa.ForeignKey("strategy_candidates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("interval", sa.String(8), nullable=False),
        sa.Column("sample", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("metrics", js, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("assumptions", js, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("error", sa.Text()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_strategy_backtests_candidate_id", "strategy_backtests", ["candidate_id"])
    op.create_index("ix_strategy_backtests_candidate_created", "strategy_backtests", ["candidate_id", "created_at"])
    op.create_index("ix_strategy_backtests_user_id", "strategy_backtests", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_strategy_backtests_user_id", table_name="strategy_backtests")
    op.drop_index("ix_strategy_backtests_candidate_created", table_name="strategy_backtests")
    op.drop_index("ix_strategy_backtests_candidate_id", table_name="strategy_backtests")
    op.drop_table("strategy_backtests")

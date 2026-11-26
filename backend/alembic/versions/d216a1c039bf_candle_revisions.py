"""Audit revisions to finalized market candles."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "d216a1c039bf"
down_revision = "ab73c8d2e410"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "market_candle_revisions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("interval", sa.String(8), nullable=False),
        sa.Column("open_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("previous", JSONB(), nullable=False),
        sa.Column("replacement", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_market_candle_revisions_lookup", "market_candle_revisions", ["symbol", "interval", "open_time"]
    )


def downgrade() -> None:
    op.drop_table("market_candle_revisions")

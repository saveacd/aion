"""add confidence to trade intents

Revision ID: c7f2a9d41b30
Revises: a040119e28e6
Create Date: 2026-11-20 00:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c7f2a9d41b30"
down_revision: str | None = "a040119e28e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "trade_intents",
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("trade_intents", "confidence")

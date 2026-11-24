"""intraday autopilot fields and per-agent model overrides

Revision ID: a7d21c4e9b10
Revises: f4c9a17d2e60
Create Date: 2026-11-20 19:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a7d21c4e9b10"
down_revision: str | None = "f4c9a17d2e60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "autopilot_configs",
        sa.Column("intraday", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "autopilot_configs",
        sa.Column(
            "max_holding_minutes",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("240"),
        ),
    )
    op.add_column(
        "user_model_settings",
        sa.Column(
            "role_models",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("user_model_settings", "role_models")
    op.drop_column("autopilot_configs", "max_holding_minutes")
    op.drop_column("autopilot_configs", "intraday")

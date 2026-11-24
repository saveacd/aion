"""autopilot watch plans (AI waiting entry zones)

Revision ID: b3e5d8f1a2c4
Revises: a7d21c4e9b10
Create Date: 2026-11-20
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "b3e5d8f1a2c4"
down_revision = "a7d21c4e9b10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "autopilot_configs",
        sa.Column(
            "watch_plans",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("autopilot_configs", "watch_plans")

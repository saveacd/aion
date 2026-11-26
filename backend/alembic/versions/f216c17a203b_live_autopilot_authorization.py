"""Persist explicit continuous live autopilot authorization."""

import sqlalchemy as sa

from alembic import op

revision = "f216c17a203b"
down_revision = "e216bead7091"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("autopilot_configs", sa.Column("live_authorized_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("autopilot_configs", "live_authorized_at")

"""Persist independently validated factor expressions and adoption windows."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "e216bead7091"
down_revision = "d216a1c039bf"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "market_funding_settlements",
        sa.Column("symbol", sa.String(32), primary_key=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("rate", sa.Numeric(38, 12), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.add_column("factor_records", sa.Column("expression", JSONB(), nullable=True))
    op.add_column("factor_records", sa.Column("discovery_state", JSONB(), nullable=True))
    op.create_index("ix_factor_records_discovery_status", "factor_records",
                    ["user_id", "symbol", "interval", "status"],
                    postgresql_where=sa.text("expression IS NOT NULL"))


def downgrade() -> None:
    # Keep all existing records. Older versions ignore DSL formulas.
    op.execute("UPDATE factor_records SET status = 'RETIRED' WHERE formula = 'FACTOR_DSL'")
    op.drop_index("ix_factor_records_discovery_status", table_name="factor_records")
    op.drop_column("factor_records", "discovery_state")
    op.drop_column("factor_records", "expression")
    op.drop_table("market_funding_settlements")

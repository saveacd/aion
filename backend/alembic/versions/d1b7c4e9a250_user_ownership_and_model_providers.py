"""user ownership scoping and admin-managed model providers

Revision ID: d1b7c4e9a250
Revises: c7f2a9d41b30
Create Date: 2026-11-20 09:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d1b7c4e9a250"
down_revision: str | None = "c7f2a9d41b30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tables whose rows belong to a single user. Admin data is backfilled so the
# existing deployment keeps working after the upgrade.
OWNED_TABLES: tuple[str, ...] = (
    "agent_runs",
    "model_calls",
    "trade_intents",
    "orders",
    "risk_decisions",
    "risk_policies",
    "risk_events",
    "account_snapshots",
    "positions_snapshots",
    "reconciliation_runs",
    "strategies",
    "exchange_accounts",
)


def upgrade() -> None:
    bind = op.get_bind()

    for table in OWNED_TABLES:
        op.add_column(table, sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True))
        op.create_foreign_key(
            f"fk_{table}_user_id_users",
            table,
            "users",
            ["user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index(f"ix_{table}_user_id", table, ["user_id"])

    admin_id = bind.execute(
        sa.text("SELECT id FROM users WHERE is_admin IS TRUE ORDER BY created_at LIMIT 1")
    ).scalar()
    if admin_id is not None:
        for table in OWNED_TABLES:
            bind.execute(
                sa.text(f"UPDATE {table} SET user_id = :uid WHERE user_id IS NULL"),
                {"uid": admin_id},
            )

    # Exchange accounts are now per-user instead of one global row per mode.
    op.drop_constraint(
        "uq_exchange_accounts_exchange_mode", "exchange_accounts", type_="unique"
    )
    op.create_unique_constraint(
        "uq_exchange_accounts_user_exchange_mode",
        "exchange_accounts",
        ["user_id", "exchange", "mode"],
    )

    op.create_table(
        "model_providers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False, server_default="openai"),
        sa.Column("base_url", sa.String(512)),
        sa.Column("api_key_enc", sa.String(2048)),
        sa.Column("light_model", sa.String(128), nullable=False, server_default=""),
        sa.Column("heavy_model", sa.String(128), nullable=False, server_default=""),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(64)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("name", name="uq_model_providers_name"),
    )
    op.create_table(
        "user_model_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "provider_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("model_providers.id", ondelete="SET NULL"),
        ),
        sa.Column("light_model", sa.String(128)),
        sa.Column("heavy_model", sa.String(128)),
        sa.Column(
            "daily_budget_usd", sa.Numeric(12, 4), nullable=False, server_default="5"
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("user_id", name="uq_user_model_settings_user_id"),
    )
    op.create_index("ix_user_model_settings_user_id", "user_model_settings", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_model_settings_user_id", table_name="user_model_settings")
    op.drop_table("user_model_settings")
    op.drop_table("model_providers")

    op.drop_constraint(
        "uq_exchange_accounts_user_exchange_mode", "exchange_accounts", type_="unique"
    )
    op.create_unique_constraint(
        "uq_exchange_accounts_exchange_mode", "exchange_accounts", ["exchange", "mode"]
    )

    for table in reversed(OWNED_TABLES):
        op.drop_index(f"ix_{table}_user_id", table_name=table)
        op.drop_constraint(f"fk_{table}_user_id_users", table, type_="foreignkey")
        op.drop_column(table, "user_id")

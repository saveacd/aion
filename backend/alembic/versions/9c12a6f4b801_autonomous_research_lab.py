"""autonomous research lab persistence

Revision ID: 9c12a6f4b801
Revises: b8e2f6a9c4d1
Create Date: 2026-11-24
"""
from __future__ import annotations
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "9c12a6f4b801"
down_revision: str | None = "b8e2f6a9c4d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    uid = postgresql.UUID(as_uuid=True)
    js = postgresql.JSONB(astext_type=sa.Text())
    op.create_table(
        "factor_research_tasks",
        sa.Column("id", uid, primary_key=True),
        sa.Column("user_id", uid, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("interval", sa.String(8), nullable=False),
        sa.Column("horizon_bars", sa.Integer(), nullable=False, server_default="8"),
        sa.Column("status", sa.String(16), nullable=False, server_default="QUEUED"),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("result", js, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("error", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "idempotency_key", name="uq_factor_research_task_idempotency"),
    )
    op.create_index("ix_factor_research_tasks_status_created", "factor_research_tasks", ["status", "created_at"])
    op.create_index("ix_factor_research_tasks_user_id", "factor_research_tasks", ["user_id"])
    op.create_table(
        "factor_validation_results",
        sa.Column("id", uid, primary_key=True),
        sa.Column("user_id", uid, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("factor_id", uid, sa.ForeignKey("factor_records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("metrics", js, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("passed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sample_definition", js, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("warnings", js, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_factor_validation_results_factor_id", "factor_validation_results", ["factor_id"])
    op.create_table(
        "factor_versions",
        sa.Column("id", uid, primary_key=True),
        sa.Column("user_id", uid, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("factor_id", uid, sa.ForeignKey("factor_records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", js, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("reason", sa.String(500), nullable=False, server_default="research"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_factor_versions_factor_id", "factor_versions", ["factor_id"])
    op.create_table(
        "factor_lifecycle_events",
        sa.Column("id", uid, primary_key=True),
        sa.Column("user_id", uid, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("factor_id", uid, sa.ForeignKey("factor_records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_status", sa.String(16)),
        sa.Column("to_status", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_factor_lifecycle_events_factor_id", "factor_lifecycle_events", ["factor_id"])
    op.create_table(
        "research_task_logs",
        sa.Column("id", uid, primary_key=True),
        sa.Column("user_id", uid, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("task_id", uid, sa.ForeignKey("factor_research_tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("level", sa.String(12), nullable=False, server_default="INFO"),
        sa.Column("event", sa.String(64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details", js, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_research_task_logs_task_id", "research_task_logs", ["task_id"])
    op.create_table(
        "factor_ensemble_versions",
        sa.Column("id", uid, primary_key=True),
        sa.Column("user_id", uid, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("interval", sa.String(8), nullable=False),
        sa.Column("regime", sa.String(24), nullable=False),
        sa.Column("weights", js, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("diagnostics", js, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "strategy_candidates",
        sa.Column("id", uid, primary_key=True),
        sa.Column("user_id", uid, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("name", sa.String(96), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="PROPOSED"),
        sa.Column("specification", js, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("source_research_task_id", uid, sa.ForeignKey("factor_research_tasks.id", ondelete="SET NULL")),
        sa.Column("evaluation", js, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "paper_strategy_sessions",
        sa.Column("id", uid, primary_key=True),
        sa.Column("user_id", uid, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("candidate_id", uid, sa.ForeignKey("strategy_candidates.id", ondelete="SET NULL")),
        sa.Column("strategy_version_id", uid, sa.ForeignKey("strategy_versions.id", ondelete="SET NULL")),
        sa.Column("name", sa.String(96), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="OBSERVING"),
        sa.Column("initial_cash", sa.Numeric(24, 8), nullable=False),
        sa.Column("current_cash", sa.Numeric(24, 8), nullable=False),
        sa.Column("metrics", js, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "paper_trade_records",
        sa.Column("id", uid, primary_key=True),
        sa.Column("user_id", uid, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("session_id", uid, sa.ForeignKey("paper_strategy_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("quantity", sa.Numeric(24, 8), nullable=False),
        sa.Column("entry_price", sa.Numeric(24, 8), nullable=False),
        sa.Column("exit_price", sa.Numeric(24, 8)),
        sa.Column("fees", sa.Numeric(24, 8), nullable=False, server_default="0"),
        sa.Column("pnl", sa.Numeric(24, 8)),
        sa.Column("status", sa.String(12), nullable=False, server_default="OPEN"),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_paper_trade_records_session_id", "paper_trade_records", ["session_id"])
    op.create_table(
        "strategy_approvals",
        sa.Column("id", uid, primary_key=True),
        sa.Column("user_id", uid, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("strategy_version_id", uid, sa.ForeignKey("strategy_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("reviewer", sa.String(96), nullable=False),
        sa.Column("note", sa.Text()),
        sa.Column("risk_check", js, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_strategy_approvals_strategy_version_id", "strategy_approvals", ["strategy_version_id"])

def downgrade() -> None:
    op.drop_index("ix_strategy_approvals_strategy_version_id", table_name="strategy_approvals")
    op.drop_table("strategy_approvals")
    op.drop_index("ix_paper_trade_records_session_id", table_name="paper_trade_records")
    op.drop_table("paper_trade_records")
    op.drop_table("paper_strategy_sessions")
    op.drop_table("strategy_candidates")
    op.drop_table("factor_ensemble_versions")
    op.drop_table("research_task_logs")
    op.drop_table("factor_lifecycle_events")
    op.drop_table("factor_versions")
    op.drop_table("factor_validation_results")
    op.drop_index("ix_factor_research_tasks_user_id", table_name="factor_research_tasks")
    op.drop_index("ix_factor_research_tasks_status_created", table_name="factor_research_tasks")
    op.drop_table("factor_research_tasks")

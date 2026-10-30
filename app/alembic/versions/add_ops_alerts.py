"""Add ops_alerts

Revision ID: add_ops_alerts
Revises: add_ops_jobs
Create Date: 2026-10-30
"""

import sqlalchemy as sa
from alembic import op

revision = "add_ops_alerts"
down_revision = "add_ops_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ops_alerts",
        sa.Column("alert_id", sa.String(36), primary_key=True),
        sa.Column("dedup_key", sa.String(160), nullable=False),
        sa.Column("rule", sa.String(40), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notify_count", sa.Integer(), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.String(64), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ops_alerts_dedup_key", "ops_alerts", ["dedup_key"])
    op.create_index("idx_ops_alerts_status", "ops_alerts", ["status"])


def downgrade() -> None:
    op.drop_table("ops_alerts")

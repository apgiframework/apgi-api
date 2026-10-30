"""Add ops_jobs durable job records and acquisition_uploads.correlation_id

Revision ID: add_ops_jobs
Revises: add_session_data_origin
Create Date: 2026-10-30
"""

import sqlalchemy as sa
from alembic import op

revision = "add_ops_jobs"
down_revision = "add_session_data_origin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("acquisition_uploads", sa.Column("correlation_id", sa.String(64), nullable=True))
    op.create_index(
        "ix_acquisition_uploads_correlation_id", "acquisition_uploads", ["correlation_id"]
    )
    op.create_table(
        "ops_jobs",
        sa.Column("job_id", sa.String(36), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("ref", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("correlation_id", sa.String(64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("enqueued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_ops_jobs_kind_ref", "ops_jobs", ["kind", "ref"])
    op.create_index("idx_ops_jobs_status", "ops_jobs", ["status"])
    op.create_index("ix_ops_jobs_correlation_id", "ops_jobs", ["correlation_id"])


def downgrade() -> None:
    op.drop_table("ops_jobs")
    op.drop_index("ix_acquisition_uploads_correlation_id", table_name="acquisition_uploads")
    op.drop_column("acquisition_uploads", "correlation_id")

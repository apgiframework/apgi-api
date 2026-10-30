"""Add administration mode, app version, browser family and enrollment partition

Revision ID: add_session_metadata
Revises: add_ops_alerts
Create Date: 2026-10-30
"""

import sqlalchemy as sa
from alembic import op

revision = "add_session_metadata"
down_revision = "add_ops_alerts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "participant_sessions", sa.Column("administration_mode", sa.String(24), nullable=True)
    )
    op.add_column("participant_sessions", sa.Column("app_version", sa.String(40), nullable=True))
    op.add_column("participant_sessions", sa.Column("browser_family", sa.String(40), nullable=True))
    op.create_index(
        "ix_participant_sessions_administration_mode",
        "participant_sessions",
        ["administration_mode"],
    )
    op.add_column("study_enrollments", sa.Column("partition", sa.String(16), nullable=True))
    op.create_index("ix_study_enrollments_partition", "study_enrollments", ["partition"])


def downgrade() -> None:
    op.drop_index("ix_study_enrollments_partition", table_name="study_enrollments")
    op.drop_column("study_enrollments", "partition")
    op.drop_index("ix_participant_sessions_administration_mode", table_name="participant_sessions")
    op.drop_column("participant_sessions", "browser_family")
    op.drop_column("participant_sessions", "app_version")
    op.drop_column("participant_sessions", "administration_mode")

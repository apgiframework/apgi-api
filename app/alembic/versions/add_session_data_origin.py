"""Add data_origin and correlation_id to participant_sessions

Revision ID: add_session_data_origin
Revises: add_explorer_event_idempotency
Create Date: 2026-10-30
"""

import sqlalchemy as sa
from alembic import op

revision = "add_session_data_origin"
down_revision = "add_explorer_event_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("participant_sessions", sa.Column("data_origin", sa.String(20), nullable=True))
    op.add_column("participant_sessions", sa.Column("correlation_id", sa.String(64), nullable=True))
    op.create_index("ix_participant_sessions_data_origin", "participant_sessions", ["data_origin"])
    op.create_index(
        "ix_participant_sessions_correlation_id", "participant_sessions", ["correlation_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_participant_sessions_correlation_id", table_name="participant_sessions")
    op.drop_index("ix_participant_sessions_data_origin", table_name="participant_sessions")
    op.drop_column("participant_sessions", "correlation_id")
    op.drop_column("participant_sessions", "data_origin")

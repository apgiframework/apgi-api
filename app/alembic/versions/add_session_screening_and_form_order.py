"""Add device tier, screening outcome and form-order columns

Revision ID: add_session_screening_and_form_order
Revises: add_acquisition_uploads
Create Date: 2026-10-28
"""

import sqlalchemy as sa
from alembic import op

revision = "add_session_screening_and_form_order"
down_revision = "add_acquisition_uploads"
branch_labels = None
depends_on = None

_SESSION_COLUMNS = [
    sa.Column("device_tier", sa.String(1), nullable=True),
    sa.Column("tier_config_version", sa.String(40), nullable=True),
    sa.Column("timing_error_distribution", sa.JSON(), nullable=True),
    sa.Column("device_fingerprint_hash", sa.String(64), nullable=True),
    sa.Column("email_hash", sa.String(64), nullable=True),
    sa.Column("screening", sa.JSON(), nullable=True),
    sa.Column("norm_eligible", sa.Boolean(), nullable=True),
]


def upgrade() -> None:
    for col in _SESSION_COLUMNS:
        op.add_column("participant_sessions", col.copy())
    for name in ("device_tier", "device_fingerprint_hash", "email_hash", "norm_eligible"):
        op.create_index(f"ix_participant_sessions_{name}", "participant_sessions", [name])
    op.add_column("study_enrollments", sa.Column("form_order", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("study_enrollments", "form_order")
    for name in ("device_tier", "device_fingerprint_hash", "email_hash", "norm_eligible"):
        op.drop_index(f"ix_participant_sessions_{name}", table_name="participant_sessions")
    for col in reversed(_SESSION_COLUMNS):
        op.drop_column("participant_sessions", col.name)

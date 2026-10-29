"""Add idempotent journal columns to trial_events

Revision ID: add_explorer_event_idempotency
Revises: add_session_screening_and_form_order
Create Date: 2026-10-28
"""

import sqlalchemy as sa
from alembic import op

revision = "add_explorer_event_idempotency"
down_revision = "add_session_screening_and_form_order"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("trial_events") as batch:
        batch.add_column(sa.Column("event_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("seq_no", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("payload_sha256", sa.String(64), nullable=True))
        batch.create_unique_constraint("uq_trial_events_session_seq", ["participant_session_id", "seq_no"])
        batch.create_unique_constraint("uq_trial_events_session_event", ["participant_session_id", "event_id"])


def downgrade() -> None:
    with op.batch_alter_table("trial_events") as batch:
        batch.drop_constraint("uq_trial_events_session_event", type_="unique")
        batch.drop_constraint("uq_trial_events_session_seq", type_="unique")
        batch.drop_column("payload_sha256")
        batch.drop_column("seq_no")
        batch.drop_column("event_id")

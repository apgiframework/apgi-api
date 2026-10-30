"""Merge the audit hash-chain branch and add trial_events.timing_provenance

Revision ID: merge_audit_chain_timing_provenance
Revises: add_session_metadata, add_audit_hash_chain
Create Date: 2026-10-30
"""

import sqlalchemy as sa
from alembic import op

revision = "merge_audit_chain_timing_provenance"
down_revision = ("add_session_metadata", "add_audit_hash_chain")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("trial_events", sa.Column("timing_provenance", sa.String(30), nullable=True))


def downgrade() -> None:
    op.drop_column("trial_events", "timing_provenance")

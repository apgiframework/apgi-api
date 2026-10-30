"""Governance tables: consent_records, item_responses, data_deletions, studies.release_state

Revision ID: add_governance_tables
Revises: merge_audit_chain_timing_provenance
Create Date: 2026-10-30

Immutability is enforced by Postgres triggers (they hold for the table owner,
unlike REVOKE): consent_records rejects UPDATE/DELETE, and
study_enrollments.partition (the holdout assignment) cannot change once set.
"""

import sqlalchemy as sa
from alembic import op

revision = "add_governance_tables"
down_revision = "merge_audit_chain_timing_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "studies",
        sa.Column("release_state", sa.String(32), nullable=False, server_default="exploratory"),
    )
    op.create_table(
        "consent_records",
        sa.Column("record_id", sa.String(36), primary_key=True),
        sa.Column("participant_id", sa.String(36), nullable=False, index=True),
        sa.Column("consent_type", sa.String(32), nullable=False, index=True),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("version", sa.String(20), nullable=True),
        sa.Column("consent_text_hash", sa.String(64), nullable=True),
        sa.Column(
            "recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "item_responses",
        sa.Column("item_response_id", sa.String(36), primary_key=True),
        sa.Column(
            "participant_session_id",
            sa.String(36),
            sa.ForeignKey("participant_sessions.participant_session_id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("event_id", sa.String(64), nullable=False),
        sa.Column("instrument_id", sa.String(64), nullable=False, index=True),
        sa.Column("instrument_version", sa.String(32), nullable=False),
        sa.Column("instrument_sha256", sa.String(64), nullable=False),
        sa.Column("item_id", sa.String(64), nullable=False),
        sa.Column("response_value", sa.JSON(), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column(
            "received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("participant_session_id", "event_id", name="uq_item_responses_event"),
        sa.UniqueConstraint(
            "participant_session_id", "instrument_id", "item_id", name="uq_item_responses_item"
        ),
    )
    op.create_table(
        "data_deletions",
        sa.Column("deletion_id", sa.String(36), primary_key=True),
        sa.Column("participant_id", sa.String(36), nullable=False, index=True),
        sa.Column("status", sa.String(16), nullable=False, index=True),
        sa.Column("requested_by", sa.String(36), nullable=True),
        sa.Column(
            "requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stores", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )

    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        CREATE FUNCTION consent_records_append_only() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'consent_records is append-only'; END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_consent_records_append_only
            BEFORE UPDATE OR DELETE ON consent_records
            FOR EACH ROW EXECUTE FUNCTION consent_records_append_only();
        CREATE FUNCTION enrollment_partition_immutable() RETURNS trigger AS $$
        BEGIN
            IF OLD.partition IS NOT NULL AND NEW.partition IS DISTINCT FROM OLD.partition THEN
                RAISE EXCEPTION 'study_enrollments.partition is immutable once set';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_enrollment_partition_immutable
            BEFORE UPDATE ON study_enrollments
            FOR EACH ROW EXECUTE FUNCTION enrollment_partition_immutable();
        """
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            DROP TRIGGER IF EXISTS trg_enrollment_partition_immutable ON study_enrollments;
            DROP FUNCTION IF EXISTS enrollment_partition_immutable();
            DROP TRIGGER IF EXISTS trg_consent_records_append_only ON consent_records;
            DROP FUNCTION IF EXISTS consent_records_append_only();
            """
        )
    op.drop_table("data_deletions")
    op.drop_table("item_responses")
    op.drop_table("consent_records")
    op.drop_column("studies", "release_state")

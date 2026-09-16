"""Add research pilot domain (participants, consent, studies, batteries,
model_versions, participant_sessions, trial_events) and enable Postgres
row-level security on participant-scoped tables.

Revision ID: add_research_pilot_domain
Revises: b1c2d3e4f5a6
Create Date: 2026-09-03 00:00:00.000000

RLS design (Postgres only — skipped entirely on SQLite, which the unit test
suite uses and which has no RLS support):

Two session GUCs are set per-request by app.services.rls.set_rls_context():
  - app.current_user_id — the authenticated caller's user_id, or '' if none.
  - app.current_role     — 'admin' | 'researcher' | '' (participant/anonymous).

A row is visible if the caller is an admin/researcher, OR the row belongs to
a Participant whose linked user_id matches the caller. This does not by
itself authorize *anonymous* participant access (no user_id) — that path is
enforced at the application layer (participant_id acts as a bearer
capability, per app/routes/participants.py) since RLS has no way to express
"caller presented the right opaque ID in the URL". RLS here is a second,
independent layer against a compromised or buggy query, not the sole
control.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "add_research_pilot_domain"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None

_RLS_TABLES_WITH_PARTICIPANT_ID = ["consents", "study_enrollments", "participant_sessions"]


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    json_type = postgresql.JSONB() if is_postgres else sa.JSON()
    uuid_default = sa.text("gen_random_uuid()") if is_postgres else None

    op.create_table(
        "participants",
        sa.Column("participant_id", sa.String(36), primary_key=True, default=uuid_default),
        sa.Column(
            "user_id", sa.String(36), sa.ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True, index=True
        ),
        sa.Column("external_ref", sa.String(255), nullable=True, index=True),
        sa.Column("encrypted_contact_email", sa.String(1024), nullable=True),
        sa.Column("encrypted_demographics", sa.String(1024), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        comment="Research pilot participants",
    )

    op.create_table(
        "studies",
        sa.Column("study_id", sa.String(36), primary_key=True, default=uuid_default),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("osf_registration_url", sa.String(500), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft", index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        comment="Research studies",
    )

    op.create_table(
        "consents",
        sa.Column("consent_id", sa.String(36), primary_key=True, default=uuid_default),
        sa.Column(
            "participant_id",
            sa.String(36),
            sa.ForeignKey("participants.participant_id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("consent_type", sa.String(30), nullable=False, index=True),
        sa.Column("version", sa.String(20), nullable=False),
        sa.Column("consent_text_hash", sa.String(64), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        comment="Versioned consent grants/revocations",
    )
    op.create_index("idx_consents_participant_type", "consents", ["participant_id", "consent_type"])
    op.create_index(
        "idx_consents_participant_type_granted", "consents", ["participant_id", "consent_type", "granted_at"]
    )

    op.create_table(
        "study_enrollments",
        sa.Column("enrollment_id", sa.String(36), primary_key=True, default=uuid_default),
        sa.Column(
            "participant_id",
            sa.String(36),
            sa.ForeignKey("participants.participant_id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "study_id", sa.String(36), sa.ForeignKey("studies.study_id", ondelete="CASCADE"), nullable=False, index=True
        ),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("participant_id", "study_id", name="uq_study_enrollments_participant_study"),
        comment="Participant <-> Study enrollment",
    )

    op.create_table(
        "batteries",
        sa.Column("battery_id", sa.String(36), primary_key=True, default=uuid_default),
        sa.Column(
            "study_id", sa.String(36), sa.ForeignKey("studies.study_id", ondelete="CASCADE"), nullable=False, index=True
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("version", sa.String(20), nullable=False),
        sa.Column("form_label", sa.String(10), nullable=False, server_default="A"),
        sa.Column("instrument_schema", json_type, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint(
            "study_id", "name", "version", "form_label", name="uq_batteries_study_name_version_form"
        ),
        comment="Behavioural test batteries",
    )

    op.create_table(
        "model_versions",
        sa.Column("model_version_id", sa.String(36), primary_key=True, default=uuid_default),
        sa.Column("version", sa.String(20), nullable=False, unique=True),
        sa.Column("feature_version", sa.String(20), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        comment="Released scoring/model pipeline versions",
    )

    op.create_table(
        "participant_sessions",
        sa.Column("participant_session_id", sa.String(36), primary_key=True, default=uuid_default),
        sa.Column(
            "participant_id",
            sa.String(36),
            sa.ForeignKey("participants.participant_id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "study_id", sa.String(36), sa.ForeignKey("studies.study_id", ondelete="CASCADE"), nullable=False, index=True
        ),
        sa.Column(
            "battery_id",
            sa.String(36),
            sa.ForeignKey("batteries.battery_id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "model_version_id",
            sa.String(36),
            sa.ForeignKey("model_versions.model_version_id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("session_index", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(20), nullable=False, server_default="scheduled", index=True),
        sa.Column("scores", json_type, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint(
            "participant_id", "study_id", "session_index", name="uq_participant_sessions_participant_study_index"
        ),
        comment="One participant's administration of one battery",
    )

    op.create_table(
        "trial_events",
        sa.Column("trial_event_id", sa.String(36), primary_key=True, default=uuid_default),
        sa.Column(
            "participant_session_id",
            sa.String(36),
            sa.ForeignKey("participant_sessions.participant_session_id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("task_type", sa.String(50), nullable=False, index=True),
        sa.Column("trial_index", sa.Integer(), nullable=False),
        sa.Column("response_value", json_type, nullable=True),
        sa.Column("rt_ms", sa.Float(), nullable=True),
        sa.Column("correct", sa.Boolean(), nullable=True),
        sa.Column("raw_gcs_object", sa.String(500), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint(
            "participant_session_id", "task_type", "trial_index", name="uq_trial_events_session_task_index"
        ),
        comment="Lightweight per-trial scoring summary; raw payload lives in Cloud Storage",
    )

    # Signed audit trail (governing doc §7.4): HMAC-SHA256 over the
    # canonical fields, computed at write time in
    # app.services.audit_signing.sign_audit_entry. NULL for rows written
    # before this migration — those remain unsigned/legacy, not tampered.
    op.add_column("audit_logs", sa.Column("signature", sa.String(64), nullable=True))

    if is_postgres:
        _enable_rls(op)


def _enable_rls(op) -> None:  # type: ignore[no-untyped-def]
    op.execute("ALTER TABLE participants ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY participants_owner_or_staff ON participants
        USING (
            current_setting('app.current_role', true) IN ('admin', 'researcher')
            OR user_id = NULLIF(current_setting('app.current_user_id', true), '')
        )
        """
    )

    for table in _RLS_TABLES_WITH_PARTICIPANT_ID:
        # table names come from the fixed, hardcoded _RLS_TABLES_WITH_PARTICIPANT_ID
        # list above, never from user input, so f-string interpolation here is safe.
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")  # nosec: B608
        op.execute(
            f"""
            CREATE POLICY {table}_owner_or_staff ON {table}
            USING (
                current_setting('app.current_role', true) IN ('admin', 'researcher')
                OR participant_id IN (
                    SELECT participant_id FROM participants
                    WHERE user_id = NULLIF(current_setting('app.current_user_id', true), '')
                )
            )
            """  # nosec: B608
        )

    # trial_events has no direct participant_id column — join through
    # participant_sessions.
    op.execute("ALTER TABLE trial_events ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY trial_events_owner_or_staff ON trial_events
        USING (
            current_setting('app.current_role', true) IN ('admin', 'researcher')
            OR participant_session_id IN (
                SELECT ps.participant_session_id
                FROM participant_sessions ps
                JOIN participants p ON p.participant_id = ps.participant_id
                WHERE p.user_id = NULLIF(current_setting('app.current_user_id', true), '')
            )
        )
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in ["trial_events"] + _RLS_TABLES_WITH_PARTICIPANT_ID + ["participants"]:
            op.execute(f"DROP POLICY IF EXISTS {table}_owner_or_staff ON {table}")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_column("audit_logs", "signature")
    op.drop_table("trial_events")
    op.drop_table("participant_sessions")
    op.drop_table("model_versions")
    op.drop_table("batteries")
    op.drop_table("study_enrollments")
    op.drop_table("consents")
    op.drop_table("studies")
    op.drop_table("participants")

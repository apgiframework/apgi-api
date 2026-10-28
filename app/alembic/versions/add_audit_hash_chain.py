"""Hash-chain the audit log (prev_record_sha256, signing_key_version) and make it append-only.

Revision ID: add_audit_hash_chain
Revises: add_acquisition_uploads
Create Date: 2026-10-28 00:00:00.000000

Append-only enforcement is a Postgres trigger (skipped on SQLite). It rejects
DELETE and any UPDATE other than the `ON DELETE SET NULL` that the users FK
performs on `user_id`; a trigger holds even for the table owner, unlike REVOKE.
"""

import sqlalchemy as sa
from alembic import op

revision = "add_audit_hash_chain"
down_revision = "add_acquisition_uploads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("audit_logs", sa.Column("chain_seq", sa.BigInteger(), nullable=True))
    op.add_column("audit_logs", sa.Column("prev_record_sha256", sa.String(64), nullable=True))
    op.add_column("audit_logs", sa.Column("signing_key_version", sa.String(32), nullable=True))
    op.create_unique_constraint("uq_audit_logs_chain_seq", "audit_logs", ["chain_seq"])

    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_logs_append_only() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'audit_logs is append-only';
            END IF;
            IF NEW.user_id IS NULL AND OLD.user_id IS NOT NULL
               AND (NEW.audit_id, NEW.action, NEW.resource_type, NEW.resource_id, NEW.details::text,
                    NEW.status, NEW.signature, NEW.chain_seq, NEW.prev_record_sha256)
                   IS NOT DISTINCT FROM
                   (OLD.audit_id, OLD.action, OLD.resource_type, OLD.resource_id, OLD.details::text,
                    OLD.status, OLD.signature, OLD.chain_seq, OLD.prev_record_sha256) THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'audit_logs is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER audit_logs_append_only BEFORE UPDATE OR DELETE ON audit_logs "
        "FOR EACH ROW EXECUTE FUNCTION audit_logs_append_only()"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS audit_logs_append_only ON audit_logs")
        op.execute("DROP FUNCTION IF EXISTS audit_logs_append_only()")
    op.drop_constraint("uq_audit_logs_chain_seq", "audit_logs", type_="unique")
    op.drop_column("audit_logs", "signing_key_version")
    op.drop_column("audit_logs", "prev_record_sha256")
    op.drop_column("audit_logs", "chain_seq")

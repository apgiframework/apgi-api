"""Bind audit metadata, add a serialised chain head and an immutable actor id.

Revision ID: audit_chain_bind_metadata
Revises: add_study_members
Create Date: 2026-11-05

- ``audit_logs.canonical_version`` (1 = legacy fields, 2 = also binds actor,
  timestamp, chain_seq and signing_key_version). Existing rows stay version 1.
- ``audit_logs.actor_id``: copy of the acting user id with no FK, so erasing a
  user (``ON DELETE SET NULL`` on ``user_id``) no longer breaks the signature.
- ``audit_chain_head``: one row locked ``FOR UPDATE`` by every append, so the
  first append on an empty chain is serialised too.
- The Postgres trigger now permits only the ``user_id`` -> NULL update and
  rejects any change to the signed metadata.
"""

import sqlalchemy as sa
from alembic import op

revision = "audit_chain_bind_metadata"
down_revision = "add_study_members"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("audit_logs", sa.Column("actor_id", sa.String(36), nullable=True))
    op.add_column(
        "audit_logs",
        sa.Column("canonical_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.execute("UPDATE audit_logs SET actor_id = user_id")
    op.create_table(
        "audit_chain_head",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("last_seq", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_record_sha256", sa.String(64), nullable=False, server_default="0" * 64),
    )
    # Seed the head from the existing chain tail, using the same canonical
    # form that wrote it, so the next append links to it.
    from app.database.models import AuditLog  # noqa: PLC0415
    from app.services.audit_signing import GENESIS_HASH, record_sha256_of_row  # noqa: PLC0415

    bind = op.get_bind()
    tail = bind.execute(
        sa.select(AuditLog.__table__).where(AuditLog.chain_seq.isnot(None)).order_by(
            AuditLog.chain_seq.desc()
        ).limit(1)
    ).first()
    if tail is None:
        seq, digest = 0, GENESIS_HASH
    else:
        seq, digest = int(tail.chain_seq), record_sha256_of_row(tail)
    bind.execute(
        sa.text("INSERT INTO audit_chain_head (id, last_seq, last_record_sha256) VALUES (1, :s, :h)"),
        {"s": seq, "h": digest},
    )

    if bind.dialect.name != "postgresql":
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
                    NEW.status, NEW.signature, NEW.chain_seq, NEW.prev_record_sha256,
                    NEW.timestamp, NEW.signing_key_version, NEW.actor_id, NEW.canonical_version)
                   IS NOT DISTINCT FROM
                   (OLD.audit_id, OLD.action, OLD.resource_type, OLD.resource_id, OLD.details::text,
                    OLD.status, OLD.signature, OLD.chain_seq, OLD.prev_record_sha256,
                    OLD.timestamp, OLD.signing_key_version, OLD.actor_id, OLD.canonical_version)
            THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'audit_logs is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )


def downgrade() -> None:
    op.drop_table("audit_chain_head")
    op.drop_column("audit_logs", "canonical_version")
    op.drop_column("audit_logs", "actor_id")

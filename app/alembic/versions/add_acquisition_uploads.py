"""Add acquisition_uploads table (apgi-system chunk ingest).

Revision ID: add_acquisition_uploads
Revises: add_nof1_experiment_engine
Create Date: 2026-10-27 00:00:01.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "add_acquisition_uploads"
down_revision = "add_nof1_experiment_engine"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "acquisition_uploads",
        sa.Column("upload_id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(128), nullable=False, index=True),
        sa.Column("object_name", sa.String(512), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("crc32c", sa.BigInteger, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("sequence", sa.Integer, nullable=True),
        sa.Column("principal", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text, nullable=False, server_default=""),
        sa.Column("committed", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "idx_acquisition_uploads_key", "acquisition_uploads", ["session_id", "object_name"]
    )


def downgrade() -> None:
    op.drop_index("idx_acquisition_uploads_key", table_name="acquisition_uploads")
    op.drop_table("acquisition_uploads")

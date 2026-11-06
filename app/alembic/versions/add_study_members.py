"""Add study_members: study-scoped staff access (NEW-16).

Revision ID: add_study_members
Revises: reconcile_orm_drift
"""

import sqlalchemy as sa
from alembic import op

revision = "add_study_members"
down_revision = "reconcile_orm_drift"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "study_members",
        sa.Column("study_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column(
            "added_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["study_id"], ["studies.study_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("study_id", "user_id"),
    )
    op.create_index("ix_study_members_user_id", "study_members", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_study_members_user_id", table_name="study_members")
    op.drop_table("study_members")

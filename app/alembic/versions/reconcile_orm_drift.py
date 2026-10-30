"""Reconcile structural drift between the migration history and the ORM.

Revision ID: reconcile_orm_drift
Revises: add_governance_tables
Create Date: 2026-10-30

Adds objects the ORM has declared but no earlier migration created: the
payments tables (orders, subscriptions, processed_webhook_events),
refresh_tokens.token_sha256 and webhook_deliveries.resolved_ip (task F06).
Comment-only and index-name differences are deliberately out of scope; the
CI drift gate (scripts/check_migration_drift.py) compares structure.
"""

import sqlalchemy as sa
from alembic import op

revision = "reconcile_orm_drift"
down_revision = "add_governance_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "processed_webhook_events",
        sa.Column(
            "event_id",
            sa.String(length=255),
            nullable=False,
            comment="Stripe Event ID (e.g., evt_...)",
        ),
        sa.Column("event_type", sa.String(length=100), nullable=False, comment="Stripe event type"),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="When the event was successfully processed",
        ),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_table(
        "orders",
        sa.Column(
            "order_id", sa.String(length=36), nullable=False, comment="Unique order identifier"
        ),
        sa.Column("user_id", sa.String(length=36), nullable=False, comment="Associated user ID"),
        sa.Column(
            "stripe_payment_intent_id",
            sa.String(length=255),
            nullable=True,
            comment="Stripe PaymentIntent ID",
        ),
        sa.Column("amount_cents", sa.Integer(), nullable=False, comment="Order amount in cents"),
        sa.Column("currency", sa.String(length=3), nullable=False, comment="Currency code"),
        sa.Column(
            "status",
            sa.String(length=50),
            nullable=False,
            comment="Order status (e.g., pending, succeeded, failed, refunded)",
        ),
        sa.Column("items", sa.JSON(), nullable=False, comment="Details of purchased items"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Creation timestamp",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Last update timestamp",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("order_id"),
    )
    op.create_table(
        "subscriptions",
        sa.Column(
            "subscription_id",
            sa.String(length=36),
            nullable=False,
            comment="Internal subscription identifier",
        ),
        sa.Column(
            "user_id",
            sa.String(length=36),
            nullable=False,
            comment="Associated user ID (1-to-1 max typically)",
        ),
        sa.Column(
            "stripe_subscription_id",
            sa.String(length=255),
            nullable=True,
            comment="Stripe Subscription ID",
        ),
        sa.Column(
            "stripe_customer_id", sa.String(length=255), nullable=True, comment="Stripe Customer ID"
        ),
        sa.Column(
            "plan_id", sa.String(length=100), nullable=False, comment="Internal plan identifier"
        ),
        sa.Column(
            "status",
            sa.String(length=50),
            nullable=False,
            comment="Subscription status (e.g., active, past_due, canceled)",
        ),
        sa.Column(
            "current_period_start",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Current billing period start",
        ),
        sa.Column(
            "current_period_end",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Current billing period end",
        ),
        sa.Column(
            "cancel_at_period_end",
            sa.Boolean(),
            nullable=False,
            comment="Cancel at period end flag",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Creation timestamp",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Last update timestamp",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("subscription_id"),
    )
    op.add_column(
        "refresh_tokens",
        sa.Column(
            "token_sha256",
            sa.String(length=64),
            nullable=True,
            comment="SHA-256 hash for fast lookup",
        ),
    )
    op.add_column(
        "webhook_deliveries",
        sa.Column(
            "resolved_ip",
            sa.String(length=45),
            nullable=True,
            comment="Resolved IP address to prevent DNS rebinding",
        ),
    )


def downgrade() -> None:
    op.drop_column("webhook_deliveries", "resolved_ip")
    op.drop_column("refresh_tokens", "token_sha256")
    op.drop_table("subscriptions")
    op.drop_table("orders")
    op.drop_table("processed_webhook_events")

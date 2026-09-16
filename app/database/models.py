"""
SQLAlchemy Database Models

ORM models for persistent storage of sessions, tasks, and user data.
"""

import uuid
from enum import Enum as PythonEnum
from typing import Optional

import sqlalchemy as sa
from sqlalchemy import JSON, Boolean, Column, DateTime
from sqlalchemy import Enum as SQLEnum
from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, declarative_base, mapped_column, relationship
from sqlalchemy.sql import func

from app.database.encryption import EncryptedString

Base = declarative_base()


# ============================================================================
# Enums
# ============================================================================


class SessionState(str, PythonEnum):
    """Session lifecycle states."""

    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"


class TaskStatus(str, PythonEnum):
    """Task execution states."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ============================================================================
# Database Models
# ============================================================================


class User(Base):  # type: ignore[misc, valid-type]
    """User account model."""

    __tablename__ = "users"

    user_id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        comment="Unique user identifier",
    )
    username = Column(
        String(100), unique=True, nullable=False, index=True, comment="Unique username"
    )
    email = Column(
        String(255), unique=True, nullable=False, index=True, comment="User email address"
    )
    password_hash = Column(String(255), nullable=False, comment="Hashed password")
    roles: Mapped[list[str]] = mapped_column(
        JSON().with_variant(ARRAY(sa.Text()), "postgresql"),
        nullable=False,
        default=list,
        comment="User roles for RBAC",
    )
    is_active = Column(
        Boolean, nullable=False, default=True, comment="Whether the user account is active"
    )
    failed_login_attempts = Column(
        Integer, nullable=False, default=0, comment="Number of consecutive failed login attempts"
    )
    locked_until = Column(
        DateTime(timezone=True), nullable=True, comment="Account lockout expiration timestamp"
    )
    mfa_secret = Column(String(255), nullable=True, comment="TOTP secret for MFA")
    mfa_enabled = Column(Boolean, nullable=False, default=False, comment="Whether MFA is enabled")
    mfa_backup_codes = Column(JSON, nullable=True, comment="One-time backup codes for MFA recovery")
    email_verification_token = Column(
        String(255), nullable=True, comment="Token for email verification"
    )
    email_verification_expires_at = Column(
        DateTime(timezone=True), nullable=True, comment="Token expiration"
    )
    password_reset_token = Column(String(255), nullable=True, comment="Token for password reset")
    password_reset_expires_at = Column(
        DateTime(timezone=True), nullable=True, comment="Password reset token expiration"
    )
    is_deleted = Column(Boolean, nullable=False, default=False, comment="Soft delete flag")
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Account creation timestamp",
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="Last update timestamp",
    )
    last_login = Column(DateTime(timezone=True), nullable=True, comment="Last login timestamp")

    # Relationships
    sessions = relationship("Session", back_populates="user", cascade="all, delete-orphan")
    session_templates = relationship(
        "SessionTemplate", back_populates="user", cascade="all, delete-orphan"
    )
    api_keys = relationship("APIKey", back_populates="user", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<User(user_id={self.user_id}, username={self.username})>"


class SessionTemplate(Base):  # type: ignore[misc, valid-type]
    """Session template model for reusable session configurations."""

    __tablename__ = "session_templates"

    template_id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        comment="Unique template identifier",
    )
    user_id = Column(
        String(36),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Owner user ID",
    )
    name = Column(String(100), nullable=False, index=True, comment="Template name")
    description = Column(Text, nullable=True, comment="Template description")
    config_path = Column(String(255), nullable=True, comment="Path to YAML configuration file")
    custom_config = Column(JSON, nullable=True, comment="Default custom configuration overrides")
    default_description = Column(Text, nullable=True, comment="Default session description")
    tags = Column(JSON, nullable=True, default=lambda: [], comment="Template tags for organization")
    is_public = Column(Boolean, nullable=False, default=False, comment="Whether template is public")
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Template creation timestamp",
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="Last update timestamp",
    )

    # Relationships
    user = relationship("User", back_populates="session_templates")
    sessions = relationship("Session", back_populates="template")

    # Indexes
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_session_templates_user_name"),
        Index("idx_session_templates_user_name", "user_id", "name"),
        Index("idx_session_templates_public", "is_public"),
        Index("idx_session_templates_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<SessionTemplate(template_id={self.template_id}, name={self.name})>"


class Session(Base):  # type: ignore[misc, valid-type]
    """Simulation session model."""

    __tablename__ = "sessions"

    session_id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        comment="Unique session identifier",
    )
    user_id = Column(
        String(36),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Owner user ID",
    )
    template_id = Column(
        String(36),
        ForeignKey("session_templates.template_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Template used to create this session",
    )
    config = Column(
        JSON,
        nullable=False,
        comment="Session configuration as JSON",
    )
    full_state = Column(
        JSON,
        nullable=True,
        comment="Full APGI system state for persistence",
    )
    state: Mapped[SessionState] = mapped_column(
        SQLEnum(SessionState, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=SessionState.CREATED,
        index=True,
        comment="Current session state",
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Session creation timestamp",
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="Last update timestamp",
    )
    description = Column(Text, nullable=True, comment="Human-readable session description")
    tags: Mapped[list[str]] = mapped_column(
        JSON().with_variant(ARRAY(String), "postgresql"),
        nullable=True,
        default=list,
        comment="Session tags for organization",
    )
    is_deleted = Column(Boolean, nullable=False, default=False, comment="Soft delete flag")

    # Relationships
    user = relationship("User", back_populates="sessions")
    template = relationship("SessionTemplate", back_populates="sessions")
    tasks = relationship("Task", back_populates="session", cascade="all, delete-orphan")
    session_data = relationship(
        "SessionData", back_populates="session", cascade="all, delete-orphan"
    )

    # Indexes
    __table_args__ = (
        Index("idx_sessions_user_created", "user_id", "created_at"),
        Index("idx_sessions_state", "state"),
        Index("idx_sessions_created_at", "created_at"),
        Index("idx_sessions_user_state", "user_id", "state"),
        Index("idx_sessions_template", "template_id"),
    )

    def __repr__(self) -> str:
        return f"<Session(session_id={self.session_id}, state={self.state})>"


class Task(Base):  # type: ignore[misc, valid-type]
    """Experimental task model."""

    __tablename__ = "tasks"

    task_id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        comment="Unique task identifier",
    )
    session_id = Column(
        String(36),
        ForeignKey("sessions.session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Associated session ID",
    )
    task_type = Column(String(50), nullable=False, index=True, comment="Type of experimental task")
    parameters = Column(JSON, nullable=False, comment="Task parameters as JSON")
    status: Mapped[TaskStatus] = mapped_column(
        SQLEnum(TaskStatus),
        name="status",
        nullable=False,
        default=TaskStatus.PENDING,
        index=True,
        comment="Current task status",
    )
    priority = Column(
        Integer, nullable=False, default=5, comment="Task priority (1=highest, 10=lowest)"
    )
    progress = Column(Integer, nullable=True, comment="Progress percentage (0-100)")
    result_data = Column(JSON, nullable=True, comment="Task results as JSON")
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Task creation timestamp",
    )
    started_at = Column(DateTime(timezone=True), nullable=True, comment="Task start timestamp")
    completed_at = Column(
        DateTime(timezone=True), nullable=True, comment="Task completion timestamp"
    )
    error_message = Column(Text, nullable=True, comment="Error message if task failed")
    webhook_url = Column(
        String(500), nullable=True, comment="Webhook URL for completion notification"
    )

    # Relationships
    session = relationship("Session", back_populates="tasks")
    dependencies = relationship(
        "TaskDependency",
        foreign_keys="TaskDependency.dependent_task_id",
        back_populates="dependent_task",
        cascade="all, delete-orphan",
    )
    prerequisite_for = relationship(
        "TaskDependency",
        foreign_keys="TaskDependency.prerequisite_task_id",
        back_populates="prerequisite_task",
        passive_deletes=True,
    )

    # Indexes
    __table_args__ = (
        Index("idx_tasks_session_created", "session_id", "created_at"),
        Index("idx_tasks_status", "status"),
        Index("idx_tasks_type", "task_type"),
        Index("idx_tasks_priority", "priority"),
    )

    def __repr__(self) -> str:
        return f"<Task(task_id={self.task_id}, type={self.task_type}, status={self.status})>"


class TaskDependency(Base):  # type: ignore[misc, valid-type]
    """Task dependency model for managing task execution order."""

    __tablename__ = "task_dependencies"

    id = Column(
        Integer, primary_key=True, autoincrement=True, comment="Auto-incrementing primary key"
    )
    dependent_task_id = Column(
        String(36),
        ForeignKey("tasks.task_id", ondelete="CASCADE"),
        nullable=False,
        comment="Task that depends on the prerequisite",
    )
    prerequisite_task_id = Column(
        String(36),
        ForeignKey("tasks.task_id", ondelete="CASCADE"),
        nullable=False,
        comment="Task that must complete before the dependent task",
    )
    dependency_type = Column(
        String(20),
        nullable=False,
        default="completion",
        comment="Type of dependency (completion, success, failure)",
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Dependency creation timestamp",
    )

    # Relationships
    dependent_task = relationship(
        "Task", foreign_keys=[dependent_task_id], back_populates="dependencies"
    )
    prerequisite_task = relationship(
        "Task", foreign_keys=[prerequisite_task_id], back_populates="prerequisite_for"
    )

    # Indexes
    __table_args__ = (
        Index("idx_task_dependencies_dependent", "dependent_task_id"),
        Index("idx_task_dependencies_prerequisite", "prerequisite_task_id"),
        Index(
            "idx_task_dependencies_unique", "dependent_task_id", "prerequisite_task_id", unique=True
        ),
    )

    def __repr__(self) -> str:
        return f"<TaskDependency(dependent={self.dependent_task_id}, prerequisite={self.prerequisite_task_id})>"


class SessionData(Base):  # type: ignore[misc, valid-type]
    """Time series data for simulation sessions."""

    __tablename__ = "session_data"

    id = Column(
        Integer, primary_key=True, autoincrement=True, comment="Auto-incrementing primary key"
    )
    session_id = Column(
        String(36),
        ForeignKey("sessions.session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Associated session ID",
    )
    time_ms = Column(Float, nullable=False, comment="Simulation time in milliseconds")
    data = Column(JSON, nullable=False, comment="State data as JSON")
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Record creation timestamp",
    )

    # Relationships
    session = relationship("Session", back_populates="session_data")

    # Indexes for efficient time-based queries
    __table_args__ = (
        Index("idx_session_data_session_time", "session_id", "time_ms"),
        Index("idx_session_data_time", "time_ms"),
    )

    def __repr__(self) -> str:
        return f"<SessionData(id={self.id}, session_id={self.session_id}, time_ms={self.time_ms})>"


class RefreshToken(Base):  # type: ignore[misc, valid-type]
    """Refresh token storage for authentication."""

    __tablename__ = "refresh_tokens"

    token_id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        comment="Unique token identifier",
    )
    user_id = Column(
        String(36),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Associated user ID",
    )
    token_hash = Column(String(255), nullable=False, comment="Hashed refresh token")
    token_sha256 = Column(
        String(64), nullable=True, index=True, comment="SHA-256 hash for fast lookup"
    )
    expires_at = Column(
        DateTime(timezone=True), nullable=False, index=True, comment="Token expiration timestamp"
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Token creation timestamp",
    )
    revoked = Column(
        Boolean, nullable=False, default=False, comment="Whether token has been revoked"
    )

    # Indexes
    __table_args__ = (
        Index("idx_refresh_tokens_user", "user_id"),
        Index("idx_refresh_tokens_expires", "expires_at"),
    )

    def __repr__(self) -> str:
        return f"<RefreshToken(token_id={self.token_id}, user_id={self.user_id})>"


class APIKey(Base):  # type: ignore[misc, valid-type]
    """API key for authentication."""

    __tablename__ = "api_keys"

    key_id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        comment="Unique API key identifier",
    )
    user_id = Column(
        String(36),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Owner user ID",
    )
    name = Column(String(100), nullable=False, comment="API key name/description")
    key_hash = Column(String(255), nullable=False, unique=True, comment="Hashed API key")
    key_prefix = Column(
        String(16), nullable=False, index=True, comment="HMAC prefix for fast lookup"
    )
    permissions = Column(
        JSON, nullable=False, default=lambda: [], comment="Permissions for this key"
    )
    expires_at = Column(
        DateTime(timezone=True),
        nullable=False,
        comment="Expiration timestamp - API keys must have an expiry date",
    )
    is_active = Column(Boolean, nullable=False, default=True, comment="Whether the key is active")
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Creation timestamp",
    )
    last_used_at = Column(DateTime(timezone=True), nullable=True, comment="Last usage timestamp")

    # Relationships
    user = relationship("User", back_populates="api_keys")

    # Indexes
    __table_args__ = (
        Index("idx_api_keys_user", "user_id"),
        Index("idx_api_keys_expires", "expires_at"),
        Index("idx_api_keys_active", "is_active"),
    )

    def __repr__(self) -> str:
        return f"<APIKey(key_id={self.key_id}, name={self.name})>"


class WebhookDelivery(Base):  # type: ignore[misc, valid-type]
    """Webhook delivery tracking."""

    __tablename__ = "webhook_deliveries"

    delivery_id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        comment="Unique delivery identifier",
    )
    task_id = Column(
        String(36),
        ForeignKey("tasks.task_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Associated task ID",
    )
    webhook_url = Column(String(500), nullable=False, comment="Target webhook URL")
    resolved_ip = Column(
        String(45), nullable=True, comment="Resolved IP address to prevent DNS rebinding"
    )
    payload = Column(JSON, nullable=False, comment="Webhook payload as JSON")
    status = Column(String(20), nullable=False, default="pending", comment="Delivery status")
    attempts = Column(Integer, nullable=False, default=0, comment="Number of delivery attempts")
    retry_count = Column(Integer, nullable=False, default=5, comment="Maximum retry attempts")
    retry_delays = Column(
        JSON,
        nullable=False,
        default=lambda: [5, 30, 300, 1800, 3600],
        comment="Retry delay schedule in seconds",
    )
    last_attempt_at = Column(
        DateTime(timezone=True), nullable=True, comment="Last delivery attempt timestamp"
    )
    next_retry_at = Column(DateTime(timezone=True), nullable=True, comment="Next retry timestamp")
    response_status = Column(Integer, nullable=True, comment="HTTP response status code")
    response_body = Column(Text, nullable=True, comment="HTTP response body")
    error_message = Column(Text, nullable=True, comment="Error message if delivery failed")
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Delivery record creation timestamp",
    )

    # Indexes
    __table_args__ = (
        Index("idx_webhook_deliveries_task", "task_id"),
        Index("idx_webhook_deliveries_status", "status"),
        Index("idx_webhook_deliveries_next_retry", "next_retry_at"),
    )

    def __repr__(self) -> str:
        return f"<WebhookDelivery(delivery_id={self.delivery_id}, status={self.status})>"


class AuditLog(Base):  # type: ignore[misc, valid-type]
    """Audit log for tracking user actions and access events."""

    __tablename__ = "audit_logs"

    audit_id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        comment="Unique audit event identifier",
    )
    user_id = Column(
        String(36),
        ForeignKey("users.user_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="User who performed the action (null for anonymous actions)",
    )
    action = Column(String(100), nullable=False, index=True, comment="Action performed")
    resource_type = Column(
        String(50), nullable=False, index=True, comment="Type of resource affected"
    )
    resource_id = Column(
        String(255), nullable=True, index=True, comment="ID of the affected resource"
    )
    timestamp = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="When the action occurred",
    )
    details = Column(JSON, nullable=True, comment="Additional details about the action")
    ip_address = Column(String(45), nullable=True, comment="Client IP address")
    user_agent = Column(Text, nullable=True, comment="Client user agent string")
    status = Column(String(20), nullable=False, default="success", comment="Action outcome")
    signature = Column(
        String(64),
        nullable=True,
        comment="HMAC-SHA256 over the canonical entry fields (app.services.audit_signing) — "
        "NULL for legacy rows written before signing was added",
    )

    # Relationships
    user = relationship("User", backref="audit_logs")

    # Indexes
    __table_args__ = (
        Index("idx_audit_logs_user_timestamp", "user_id", "timestamp"),
        Index("idx_audit_logs_resource", "resource_type", "resource_id"),
        Index("idx_audit_logs_timestamp", "timestamp"),
    )

    def __repr__(self) -> str:
        return f"<AuditLog(audit_id={self.audit_id}, user_id={self.user_id}, action={self.action})>"


class Order(Base):  # type: ignore[misc, valid-type]
    """Order for single purchases like a lifetime license."""

    __tablename__ = "orders"

    order_id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        comment="Unique order identifier",
    )
    user_id = Column(
        String(36),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Associated user ID",
    )
    stripe_payment_intent_id = Column(
        String(255), nullable=True, index=True, comment="Stripe PaymentIntent ID"
    )
    amount_cents = Column(Integer, nullable=False, comment="Order amount in cents")
    currency = Column(String(3), nullable=False, default="usd", comment="Currency code")
    status = Column(
        String(50),
        nullable=False,
        default="pending",
        index=True,
        comment="Order status (e.g., pending, succeeded, failed, refunded)",
    )
    items = Column(JSON, nullable=False, comment="Details of purchased items")
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Creation timestamp",
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="Last update timestamp",
    )

    # Relationships
    user = relationship("User", backref="orders")


class Subscription(Base):  # type: ignore[misc, valid-type]
    """Subscription model for recurring billing and entitlements."""

    __tablename__ = "subscriptions"

    subscription_id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        comment="Internal subscription identifier",
    )
    user_id = Column(
        String(36),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        unique=True,
        comment="Associated user ID (1-to-1 max typically)",
    )
    stripe_subscription_id = Column(
        String(255), nullable=True, unique=True, index=True, comment="Stripe Subscription ID"
    )
    stripe_customer_id = Column(
        String(255), nullable=True, index=True, comment="Stripe Customer ID"
    )
    plan_id = Column(String(100), nullable=False, comment="Internal plan identifier")
    status = Column(
        String(50),
        nullable=False,
        default="active",
        index=True,
        comment="Subscription status (e.g., active, past_due, canceled)",
    )
    current_period_start = Column(
        DateTime(timezone=True), nullable=True, comment="Current billing period start"
    )
    current_period_end = Column(
        DateTime(timezone=True), nullable=True, comment="Current billing period end"
    )
    cancel_at_period_end = Column(
        Boolean, nullable=False, default=False, comment="Cancel at period end flag"
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Creation timestamp",
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="Last update timestamp",
    )

    # Relationships
    user = relationship("User", backref="subscription", uselist=False)


class ProcessedWebhookEvent(Base):  # type: ignore[misc, valid-type]
    """Track processed Stripe webhook event IDs to ensure idempotency."""

    __tablename__ = "processed_webhook_events"

    event_id = Column(
        String(255),
        primary_key=True,
        comment="Stripe Event ID (e.g., evt_...)",
    )
    event_type = Column(String(100), nullable=False, index=True, comment="Stripe event type")
    processed_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="When the event was successfully processed",
    )

    def __repr__(self) -> str:
        return f"<ProcessedWebhookEvent(event_id={self.event_id}, type={self.event_type})>"


# ============================================================================
# Research Pilot Domain (Phase 2, governing doc §7)
#
# Participants, consent, studies, batteries, participant sessions, trial
# events, and model versions — the schema behind the APGI research pilot.
# Deliberately separate from the generic User/Session/Task simulation domain
# above: a Participant is a human research subject, not an API account
# (though a Participant MAY be linked to a User if the pilot requires
# authenticated access — see Participant.user_id).
# ============================================================================


class ConsentType(str, PythonEnum):
    """The two independently versioned consents every participant must grant."""

    RESEARCH_PARTICIPATION = "research_participation"
    DATA_SHARING = "data_sharing"


class StudyStatus(str, PythonEnum):
    """Lifecycle of a research study."""

    DRAFT = "draft"
    ACTIVE = "active"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class ParticipantSessionStatus(str, PythonEnum):
    """Lifecycle of a single participant's administration of a battery."""

    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class Participant(Base):  # type: ignore[misc, valid-type]
    """A research pilot participant.

    Contact/demographic fields are encrypted at the column level
    (app/database/encryption.py) — even a full database dump does not expose
    PII without PII_ENCRYPTION_KEY. `is_deleted` implements the erasure
    contract for app/tasks/deletion_tasks.py: encrypted fields are
    NULLed out immediately, and the row itself is retained (tombstoned)
    only long enough to preserve referential integrity for de-identified
    scoring records already exported.
    """

    __tablename__ = "participants"

    participant_id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        comment="Unique participant identifier",
    )
    user_id = Column(
        String(36),
        ForeignKey("users.user_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Linked API user account, if the participant has authenticated access",
    )
    external_ref = Column(
        String(255),
        nullable=True,
        index=True,
        comment="Pseudonymous external panel/recruitment ID (e.g. Prolific ID) — not itself PII",
    )
    encrypted_contact_email: Mapped[Optional[str]] = mapped_column(
        EncryptedString(),
        nullable=True,
        comment="Encrypted contact email (PII)",
    )
    encrypted_demographics: Mapped[Optional[str]] = mapped_column(
        EncryptedString(),
        nullable=True,
        comment="Encrypted JSON blob of self-reported demographics (PII)",
    )
    is_deleted = Column(
        Boolean, nullable=False, default=False, comment="Right-to-erasure tombstone flag"
    )
    deleted_at = Column(DateTime(timezone=True), nullable=True, comment="Erasure timestamp")
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Enrollment timestamp",
    )

    consents = relationship("Consent", back_populates="participant", cascade="all, delete-orphan")
    enrollments = relationship(
        "StudyEnrollment", back_populates="participant", cascade="all, delete-orphan"
    )
    sessions = relationship(
        "ParticipantSession", back_populates="participant", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("idx_participants_user", "user_id"),)

    def __repr__(self) -> str:
        return f"<Participant(participant_id={self.participant_id})>"


class Consent(Base):  # type: ignore[misc, valid-type]
    """A single versioned consent grant or revocation.

    Consent is append-only: granting a new version, or revoking, inserts a
    new row rather than mutating history — the full consent record is
    itself part of the audit trail (governing doc §7.1). The *effective*
    consent for a type is the most recent row with revoked_at IS NULL.
    """

    __tablename__ = "consents"

    consent_id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        comment="Unique consent record identifier",
    )
    participant_id = Column(
        String(36),
        ForeignKey("participants.participant_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Consenting participant",
    )
    consent_type: Mapped[ConsentType] = mapped_column(
        SQLEnum(ConsentType, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        index=True,
        comment="Which of the two required consents this record grants/revokes",
    )
    version = Column(String(20), nullable=False, comment="Version of the consent text granted")
    consent_text_hash = Column(
        String(64),
        nullable=False,
        comment="SHA-256 of the exact consent text shown, for tamper-evident audit",
    )
    granted_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="When this consent was granted",
    )
    revoked_at = Column(
        DateTime(timezone=True), nullable=True, comment="When this consent was revoked, if ever"
    )
    ip_address = Column(String(45), nullable=True, comment="Client IP address at grant time")

    participant = relationship("Participant", back_populates="consents")

    __table_args__ = (
        Index("idx_consents_participant_type", "participant_id", "consent_type"),
        Index(
            "idx_consents_participant_type_granted", "participant_id", "consent_type", "granted_at"
        ),
    )

    def __repr__(self) -> str:
        return f"<Consent(participant_id={self.participant_id}, type={self.consent_type}, v={self.version})>"


class Study(Base):  # type: ignore[misc, valid-type]
    """A research study — an OSF-registered protocol administering one or more batteries."""

    __tablename__ = "studies"

    study_id = Column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4()), comment="Unique study ID"
    )
    name = Column(String(200), nullable=False, comment="Study name")
    description = Column(Text, nullable=True, comment="Study description")
    osf_registration_url = Column(
        String(500), nullable=True, comment="OSF preregistration URL (identifiers.yaml: osf)"
    )
    status: Mapped[StudyStatus] = mapped_column(
        SQLEnum(StudyStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=StudyStatus.DRAFT,
        index=True,
        comment="Study lifecycle status",
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Creation timestamp",
    )

    enrollments = relationship("StudyEnrollment", back_populates="study")
    batteries = relationship("Battery", back_populates="study")
    sessions = relationship("ParticipantSession", back_populates="study")

    def __repr__(self) -> str:
        return f"<Study(study_id={self.study_id}, name={self.name})>"


class StudyEnrollment(Base):  # type: ignore[misc, valid-type]
    """Join table: which participants are enrolled in which studies."""

    __tablename__ = "study_enrollments"

    enrollment_id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        comment="Unique enrollment identifier",
    )
    participant_id = Column(
        String(36),
        ForeignKey("participants.participant_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    study_id = Column(
        String(36),
        ForeignKey("studies.study_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    enrolled_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Enrollment timestamp",
    )

    participant = relationship("Participant", back_populates="enrollments")
    study = relationship("Study", back_populates="enrollments")

    __table_args__ = (
        UniqueConstraint(
            "participant_id", "study_id", name="uq_study_enrollments_participant_study"
        ),
    )

    def __repr__(self) -> str:
        return f"<StudyEnrollment(participant_id={self.participant_id}, study_id={self.study_id})>"


class Battery(Base):  # type: ignore[misc, valid-type]
    """A behavioural test battery (jsPsych tasks administered by apgi-battery).

    `form_label` distinguishes parallel forms of the same battery (e.g. "A"
    vs "B") administered across repeated sessions to reduce practice
    effects — see app/services/scoring.py's parallel-form handling.
    """

    __tablename__ = "batteries"

    battery_id = Column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4()), comment="Unique battery ID"
    )
    study_id = Column(
        String(36),
        ForeignKey("studies.study_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(200), nullable=False, comment="Battery name")
    version = Column(
        String(20),
        nullable=False,
        comment="Battery version (identifiers.yaml: release_state.battery_version)",
    )
    form_label = Column(String(10), nullable=False, default="A", comment="Parallel-form label")
    instrument_schema = Column(
        JSON, nullable=True, comment="Task/item schema describing this battery's contents"
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Creation timestamp",
    )

    study = relationship("Study", back_populates="batteries")
    sessions = relationship("ParticipantSession", back_populates="battery")

    __table_args__ = (
        UniqueConstraint(
            "study_id", "name", "version", "form_label", name="uq_batteries_study_name_version_form"
        ),
    )

    def __repr__(self) -> str:
        return f"<Battery(battery_id={self.battery_id}, name={self.name}, form={self.form_label})>"


class ModelVersion(Base):  # type: ignore[misc, valid-type]
    """A released version of the scoring/model pipeline (apgi-analysis).

    Threads identifiers.yaml's release_state.model_version through scored
    data so every score can be traced to the exact pipeline that produced
    it — required before release_state.current may ever advance to
    "calibrated" (K1-K3 gates, governing doc §0.0).
    """

    __tablename__ = "model_versions"

    model_version_id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        comment="Unique model version identifier",
    )
    version = Column(String(20), nullable=False, unique=True, comment="Semantic model version")
    feature_version = Column(String(20), nullable=True, comment="Feature-extraction version")
    description = Column(Text, nullable=True, comment="What changed in this version")
    is_active = Column(
        Boolean,
        nullable=False,
        default=False,
        comment="Whether this is the version new sessions are scored against",
    )
    released_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Release timestamp",
    )

    sessions = relationship("ParticipantSession", back_populates="model_version")

    def __repr__(self) -> str:
        return f"<ModelVersion(version={self.version}, active={self.is_active})>"


class ParticipantSession(Base):  # type: ignore[misc, valid-type]
    """One participant's administration of one battery within one study.

    `session_index` numbers repeated administrations for the same
    participant+study (1, 2, 3, ...) — the basis for longitudinal metrics
    (Phase 5: ICC/SEM/MDC95 per index) and for selecting which parallel
    battery form to serve next.
    """

    __tablename__ = "participant_sessions"

    participant_session_id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        comment="Unique participant session identifier",
    )
    participant_id = Column(
        String(36),
        ForeignKey("participants.participant_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    study_id = Column(
        String(36),
        ForeignKey("studies.study_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    battery_id = Column(
        String(36),
        ForeignKey("batteries.battery_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    model_version_id = Column(
        String(36),
        ForeignKey("model_versions.model_version_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Set once scoring runs against this session",
    )
    session_index = Column(
        Integer,
        nullable=False,
        default=1,
        comment="1-based repeated-measures index for this participant+study",
    )
    status: Mapped[ParticipantSessionStatus] = mapped_column(
        SQLEnum(ParticipantSessionStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=ParticipantSessionStatus.SCHEDULED,
        index=True,
    )
    scores = Column(JSON, nullable=True, comment="Computed per-index scores once scoring completes")
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Creation timestamp",
    )

    participant = relationship("Participant", back_populates="sessions")
    study = relationship("Study", back_populates="sessions")
    battery = relationship("Battery", back_populates="sessions")
    model_version = relationship("ModelVersion", back_populates="sessions")
    trial_events = relationship(
        "TrialEvent", back_populates="participant_session", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint(
            "participant_id",
            "study_id",
            "session_index",
            name="uq_participant_sessions_participant_study_index",
        ),
        Index("idx_participant_sessions_participant", "participant_id"),
        Index("idx_participant_sessions_study", "study_id"),
        Index("idx_participant_sessions_status", "status"),
    )

    def __repr__(self) -> str:
        return f"<ParticipantSession(id={self.participant_session_id}, index={self.session_index}, status={self.status})>"

    @property
    def release_state(self) -> str:
        """The project's current release_state.current label (identifiers.yaml),
        threaded onto every session response so a client can tell whether
        scores were produced under "pre-registered" / "pilot" / "calibrated"
        conditions. Not a column — computed at read time so it always
        reflects the live value rather than the value at scoring time.
        Imported lazily (not at module scope) because app.services'
        package __init__ imports modules that import app.database.models,
        and models.py is loaded before that init completes."""
        from app.services.identifiers import current_release_state

        return current_release_state()


class TrialEvent(Base):  # type: ignore[misc, valid-type]
    """One trial's lightweight, scoring-relevant summary.

    The full raw event (RT distributions, keystroke/mouse logs, stimulus
    payloads) is written to restricted Cloud Storage by
    app/services/trial_storage.py; `raw_gcs_object` names that object. This
    table stores only what scoring needs so scoring never has to read GCS
    on the hot path, and so a GCS outage doesn't block trial ingestion's
    acknowledgement to the client (the object write is attempted inline,
    but this row is the durable record either way).
    """

    __tablename__ = "trial_events"

    trial_event_id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        comment="Unique trial event identifier",
    )
    participant_session_id = Column(
        String(36),
        ForeignKey("participant_sessions.participant_session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    task_type = Column(
        String(50), nullable=False, index=True, comment="Which task this trial belongs to"
    )
    trial_index = Column(Integer, nullable=False, comment="0-based trial index within the task")
    response_value = Column(JSON, nullable=True, comment="Scoring-relevant response summary")
    rt_ms = Column(Float, nullable=True, comment="Response time in milliseconds")
    correct = Column(
        Boolean, nullable=True, comment="Whether the response was correct, if applicable"
    )
    raw_gcs_object = Column(
        String(500), nullable=True, comment="Object path in the restricted raw-trial-events bucket"
    )
    received_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Ingestion timestamp",
    )

    participant_session = relationship("ParticipantSession", back_populates="trial_events")

    __table_args__ = (
        UniqueConstraint(
            "participant_session_id",
            "task_type",
            "trial_index",
            name="uq_trial_events_session_task_index",
        ),
        Index("idx_trial_events_session", "participant_session_id"),
    )

    def __repr__(self) -> str:
        return f"<TrialEvent(id={self.trial_event_id}, task={self.task_type}, trial={self.trial_index})>"


# ============================================================================
# n-of-1 Experiment Engine (Phase 5)
#
# Single-subject experiments with an experimenter-defined alternating-phase
# design (e.g. ["A", "B", "A", "B"]) and a scalar outcome recorded once per
# observation — deliberately decoupled from ParticipantSession/TrialEvent,
# since an n-of-1 outcome (e.g. a daily self-report) need not correspond to
# a full battery administration.
# ============================================================================


class NOf1ExperimentStatus(str, PythonEnum):
    ACTIVE = "active"
    COMPLETED = "completed"


class NOf1Experiment(Base):  # type: ignore[misc, valid-type]
    """A single-subject alternating-phase experiment."""

    __tablename__ = "nof1_experiments"

    experiment_id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        comment="Unique experiment ID",
    )
    participant_id = Column(
        String(36),
        ForeignKey("participants.participant_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    study_id = Column(
        String(36),
        ForeignKey("studies.study_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    name = Column(String(200), nullable=False, comment="Experiment name")
    phase_sequence: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, comment='Ordered phase labels, e.g. ["A", "B", "A", "B"]'
    )
    outcome_metric_name = Column(
        String(100), nullable=False, comment="Name of the scalar outcome being tracked"
    )
    status: Mapped[NOf1ExperimentStatus] = mapped_column(
        SQLEnum(NOf1ExperimentStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=NOf1ExperimentStatus.ACTIVE,
        index=True,
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Creation timestamp",
    )

    participant = relationship("Participant")
    observations = relationship(
        "NOf1Observation", back_populates="experiment", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("idx_nof1_experiments_participant", "participant_id"),)

    def __repr__(self) -> str:
        return f"<NOf1Experiment(id={self.experiment_id}, name={self.name})>"


class NOf1Observation(Base):  # type: ignore[misc, valid-type]
    """A single recorded outcome value within an n-of-1 experiment."""

    __tablename__ = "nof1_observations"

    observation_id = Column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        comment="Unique observation ID",
    )
    experiment_id = Column(
        String(36),
        ForeignKey("nof1_experiments.experiment_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence_index = Column(
        Integer, nullable=False, comment="0-based order of this observation within the experiment"
    )
    phase_label = Column(
        String(20), nullable=False, index=True, comment="Which phase this observation belongs to"
    )
    value = Column(Float, nullable=False, comment="Recorded outcome value")
    recorded_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Recording timestamp",
    )

    experiment = relationship("NOf1Experiment", back_populates="observations")

    __table_args__ = (
        UniqueConstraint(
            "experiment_id", "sequence_index", name="uq_nof1_observations_experiment_sequence"
        ),
        Index("idx_nof1_observations_experiment", "experiment_id"),
    )

    def __repr__(self) -> str:
        return f"<NOf1Observation(experiment_id={self.experiment_id}, phase={self.phase_label}, value={self.value})>"

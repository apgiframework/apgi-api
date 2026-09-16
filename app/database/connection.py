"""
Database Connection Management

SQLAlchemy engine and session configuration.
"""

import logging
import secrets
import string
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from typing import Any, AsyncGenerator, Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.database.models import Base, User

# record_query_timing removed to avoid circular import

logger = logging.getLogger(__name__)


# Create SQLAlchemy engine with connection pooling
if settings.database_url.startswith("sqlite"):
    # SQLite-specific configuration
    engine = create_engine(
        settings.database_url,
        echo=False,  # Set to True for SQL query logging
        connect_args={"check_same_thread": False},  # SQLite specific
    )
else:  # pragma: no cover
    # PostgreSQL configuration with connection pooling optimized for horizontal scaling
    engine = create_engine(
        settings.database_url,
        echo=False,  # Set to True for SQL query logging
        pool_pre_ping=True,  # Verify connections before using
        pool_size=settings.pool_size,  # Configurable pool size
        max_overflow=settings.max_overflow,  # Configurable overflow
        pool_timeout=settings.pool_timeout,  # Configurable timeout
        pool_recycle=settings.pool_recycle,  # Configurable recycle
        echo_pool=False,  # Disable pool logging in production
    )


# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def generate_secure_password(length: int = 32) -> str:
    """
    Generate a secure random password that meets validation requirements.

    Ensures password contains at least one uppercase letter, one lowercase letter,
    and one digit as required by the API's password validation.

    Args:
        length: Length of the password to generate

    Returns:
        Secure random password string meeting validation requirements
    """
    if length < 3:
        raise ValueError("Password length must be at least 3")

    # Required character sets
    uppercase = string.ascii_uppercase
    lowercase = string.ascii_lowercase
    digits = string.digits
    special = "!@#$%^&*()_+-=[]{}|;:,.<>?"

    # Ensure at least one of each required type
    password_chars = [
        secrets.choice(uppercase),
        secrets.choice(lowercase),
        secrets.choice(digits),
    ]

    # Fill remaining with mixed characters
    all_chars = uppercase + lowercase + digits + special
    remaining = length - len(password_chars)
    password_chars.extend(secrets.choice(all_chars) for _ in range(remaining))

    # Shuffle to avoid predictable patterns
    secrets.SystemRandom().shuffle(password_chars)

    return "".join(password_chars)


def generate_secure_username(prefix: str = "user") -> str:
    """
    Generate a secure random username.

    Args:
        prefix: Prefix for the username

    Returns:
        Secure random username string
    """
    random_suffix = secrets.token_hex(8)
    return f"{prefix}_{random_suffix}"


def init_db() -> None:
    """
    Initialize database by creating all tables and default user.

    This should be called during application startup.
    """
    try:
        # Create all tables
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables created successfully")

        # Create default user if it doesn't exist
        create_default_user()

    except Exception as e:
        logger.error(f"Failed to create database tables: {e}")
        raise


def create_default_user() -> None:
    """
    Create a default user for session management with secure random credentials.

    This ensures that the foreign key constraint for sessions
    can be satisfied when creating sessions without explicit user management.

    SECURITY NOTE: Credentials are logged ONCE at WARNING level during creation.
    In production, use the CLI bootstrap command or environment variables instead.
    """
    db = SessionLocal()
    try:
        # Check if any default user already exists (users with username starting with "default_")
        from sqlalchemy import select

        stmt = select(User).where(User.username.like("default_%"))
        result = db.execute(stmt)
        existing_user = result.scalar_one_or_none()

        if existing_user:
            logger.info(f"Default user already exists: {existing_user.username}")
            return

        # Generate secure credentials
        secure_username = generate_secure_username("default")
        secure_password = generate_secure_password()

        # Import here to avoid circular import
        from app.services.auth_manager import AuthManager

        # Create default user with proper UUID
        user_id = str(uuid.uuid4())
        default_user = User(
            user_id=user_id,
            username=secure_username,
            email=f"{secure_username}@apgi-system.local",
            password_hash=AuthManager.hash_password(secure_password),
            roles=["user", "session_manager"],
            is_active=True,  # Explicitly set is_active to satisfy database schema
        )

        db.add(default_user)
        db.commit()

        # Log credentials ONCE via structured logging with explicit warnings
        # This is more secure than temp files as logs can be directed to secure sinks
        logger.warning(
            "DEFAULT_USER_CREATED",
            extra={
                "event": "default_user_created",
                "username": secure_username,
                "user_id": user_id,
                "password": secure_password,  # Logged once for initial setup
                "log_message": (
                    f"Default user created: {secure_username}. "
                    f"User ID: {user_id}. "
                    f"Password shown once for initial setup. "
                    "ACTION REQUIRED: Change password immediately via API or CLI. "
                    "In production, use: python -m app.cli bootstrap-admin"
                ),
            },
        )

        logger.info(
            "Default user created with secure credentials. "
            "Password was logged once at WARNING level. "
            "Search logs for 'DEFAULT_USER_CREATED' to retrieve."
        )

    except Exception as e:
        db.rollback()
        logger.error(f"Failed to create default user: {e}")
        raise
    finally:
        db.close()


def get_db() -> Generator[Session, None, None]:
    """
    Dependency function to get database session.

    Yields:
        Session: SQLAlchemy database session

    Usage:
        @app.get("/endpoint")
        async def endpoint(db: Session = Depends(get_db)):
            # Use db session
            pass
    """
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def get_db_context() -> Generator[Session, None, None]:
    """
    Context manager for database session.

    Yields:
        Session: SQLAlchemy database session

    Usage:
        with get_db_context() as db:
            # Use db session
            pass
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@asynccontextmanager
async def get_async_db_context() -> AsyncGenerator[Session, None]:
    """
    Async context manager for database session.

    Yields:
        Session: SQLAlchemy database session

    Usage:
        async with get_async_db_context() as db:
            # Use db session
            pass
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def close_db() -> None:
    """
    Close database connections.

    This should be called during application shutdown.
    """
    try:
        engine.dispose()
        logger.info("Database connections closed")
    except Exception as e:
        logger.error(f"Error closing database connections: {e}")


def get_pool_status() -> dict[str, Any]:
    """
    Get database connection pool status for monitoring.

    Returns:
        Dictionary with pool status information
    """
    pool = engine.pool

    def _pool_stat(name: str) -> int:
        value = getattr(pool, name, 0)
        return value() if callable(value) else value

    status: dict[str, Any] = {
        "pool_size": _pool_stat("size"),
        "checked_in": _pool_stat("checkedin"),
        "checked_out": _pool_stat("checkedout"),
        "overflow": _pool_stat("overflow"),
        "invalid": _pool_stat("invalid"),
        "timeout": _pool_stat("timeout"),
    }

    # Calculate utilization
    total_connections = status["checked_in"] + status["checked_out"]
    max_connections = status["pool_size"] + status["overflow"]
    utilization = total_connections / max_connections if max_connections > 0 else 0

    status["utilization"] = utilization
    status["available_connections"] = max_connections - total_connections

    # Log warnings if pool is under stress
    if utilization > 0.8:
        logger.warning(
            f"Database connection pool under high load: {utilization:.1%} utilized "
            f"({total_connections}/{max_connections} connections)"
        )
    elif status["checked_out"] >= status["pool_size"] + status["overflow"]:  # pragma: no cover
        logger.error(
            f"Database connection pool exhausted: {status['checked_out']} connections checked out, "
            f"pool_size={status['pool_size']}, overflow={status['overflow']}"
        )

    return status


def log_pool_status() -> None:
    """
    Log current database connection pool status.
    """
    status = get_pool_status()
    logger.info(
        f"DB Pool Status: size={status['pool_size']}, checked_out={status['checked_out']}, "
        f"checked_in={status['checked_in']}, overflow={status['overflow']}, "
        f"utilization={status['utilization']:.1%}"
    )


# SQLAlchemy event listeners for query profiling
@event.listens_for(Engine, "before_cursor_execute")
def before_cursor_execute(
    conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
) -> None:
    context._query_start_time = time.time()


@event.listens_for(Engine, "after_cursor_execute")
def after_cursor_execute(
    conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
) -> None:
    total_time = time.time() - context._query_start_time
    # Use a hash of the statement as the query_hash
    import hashlib

    query_hash = hashlib.sha256(statement.encode()).hexdigest()

    # Record timing
    try:
        from app.middleware.db_profiling import record_query_timing

        record_query_timing(
            query_hash=query_hash,
            query_text=statement,
            duration_ms=total_time * 1000,
            rows=cursor.rowcount,
        )
    except (ImportError, AttributeError):  # pragma: no cover
        pass

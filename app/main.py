"""
APGI REST API Main Application

FastAPI application providing RESTful access to the APGI System.

All CLI commands with 1unique options:

--revision TEXT / -r TEXT
--verbose / -v
--queues TEXT / -Q TEXT
--concurrency INTEGER / -c INTEGER
--loglevel TEXT / -l TEXT
--hostname TEXT / -n TEXT
--users INTEGER / -u INTEGER
--templates INTEGER / -t INTEGER
--sessions INTEGER / -s INTEGER
--tasks INTEGER / -T INTEGER
--clear / -c
--confirm

"""

import os
import socket
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Dict, Optional

import redis.asyncio as redis
from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles  # noqa: F401

from app.config import settings
from app.database.connection import close_db, init_db
from app.exception_handlers import register_exception_handlers
from app.middleware.alerting import configure_alerting
from app.middleware.api_versioning import APIVersioningMiddleware
from app.middleware.authentication import AuthenticationMiddleware
from app.middleware.cors_config import configure_cors
from app.middleware.csrf import CSRFMiddleware
from app.middleware.deprecation import DeprecationMiddleware
from app.middleware.logging import (
    RequestLoggingMiddleware,
    StructuredLogger,
    configure_structured_logging,
)
from app.middleware.metrics import PrometheusMetricsMiddleware
from app.middleware.profiling import ProfilingMiddleware
from app.middleware.rate_limiting import RateLimitingMiddleware
from app.middleware.request_size_limit import RequestSizeLimitMiddleware
from app.middleware.schema_validation import ResponseSchemaValidationMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.middleware.security_validation import SecurityValidationMiddleware
from app.routes import (
    admin,
    api_keys,
    auth,
    batteries,
    export,
    health,
    instrument,
    longitudinal,
    meta,
    metrics,
    nof1,
    norms,
    participants,
    payments,
    sessions,
    state,
    studies,
    tasks,
    templates,
    trials,
    users,
    version,
    webhooks,
)

# OpenTelemetry import is conditional - handle ImportError
try:
    from app.middleware.tracing import configure_distributed_tracing

    OPENTELEMETRY_AVAILABLE = True
except ImportError:
    OPENTELEMETRY_AVAILABLE = False
from app.schemas.root import RootResponse
from app.services.cache_service import init_cache_service

# Configure structured logging
configure_structured_logging(settings.log_level)
logger: StructuredLogger = StructuredLogger(__name__)


# Global Redis client
redis_client: Optional[redis.Redis] = None


def is_port_available(host: str, port: int) -> bool:
    """Check if a port is available on the given host."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
            return True
    except OSError:
        return False


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan context manager for startup and shutdown events."""
    global redis_client

    # Startup
    logger.info("Application starting up", component="lifecycle")

    # Skip dependency validation in test mode or when TEST_MODE is enabled.
    if getattr(app.state, "test_mode", False) or os.environ.get("TEST_MODE") == "true":
        logger.info("Skipping dependency check in test mode", component="lifecycle")
    else:
        # Validate hard dependencies first - these are non-negotiable
        from app.dependency_checker import HARD_DEPENDENCIES, check_dependencies

        if not check_dependencies(required_deps=HARD_DEPENDENCIES, fail_fast=False):
            logger.error("Hard dependency check failed. Cannot start.", component="lifecycle")
            raise RuntimeError("Hard dependency check failed. See logs for details.")

        # Check soft dependencies - log warnings but don't fail
        from app.dependency_checker import SOFT_DEPENDENCIES

        if not check_dependencies(
            required_deps=SOFT_DEPENDENCIES, fail_fast=False
        ):  # pragma: no cover
            logger.warning(
                "Soft dependencies missing. Starting in degraded mode.", component="lifecycle"
            )
            app.state.degraded_mode = True
        else:  # pragma: no cover
            app.state.degraded_mode = False

    # Configure alerting system
    configure_alerting(
        webhook_urls=settings.alert_webhook_urls,
        enable_log_channel=settings.alert_enable_log_channel,
        error_rate_threshold=settings.alert_error_rate_threshold,
        error_rate_window_minutes=settings.alert_error_rate_window_minutes,
        alert_cooldown_minutes=settings.alert_cooldown_minutes,
    )
    logger.info("Alerting system configured", component="alerting")

    # Configure distributed tracing
    if OPENTELEMETRY_AVAILABLE:
        configure_distributed_tracing()
        logger.info("Distributed tracing configured", component="tracing")
    else:
        logger.info(
            "Distributed tracing disabled (OpenTelemetry not available)", component="tracing"
        )

    # Initialize database
    try:
        init_db()
        logger.info("Database initialized", component="database")
        app.state.db_available = True
    except Exception as e:
        logger.error("Failed to initialize database", component="database", error=str(e))
        if settings.environment == "production":
            raise
        logger.warning("Continuing in degraded mode without database", component="database")
        app.state.db_available = False
        app.state.degraded_mode = True

    # Initialize Redis client
    try:
        redis_client = redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
        if redis_client:
            await redis_client.ping()
            logger.info("Redis client initialized", component="redis", url=settings.redis_url)
            app.state.redis_available = True

            # Initialize cache service
            init_cache_service(redis_client)
            logger.info("Cache service initialized", component="cache")

            # Update rate limiting middleware with Redis client
            RateLimitingMiddleware.set_redis_client(redis_client)
            logger.info(
                "Rate limiting middleware updated with Redis client", component="middleware"
            )
        else:
            logger.error("Failed to initialize Redis client", component="redis")
            app.state.redis_available = False
            app.state.degraded_mode = True

    except Exception as e:
        logger.error("Failed to initialize Redis", component="redis", error=str(e))
        app.state.redis_available = False
        app.state.degraded_mode = True
        logger.warning("Continuing in degraded mode without Redis", component="redis")

    # Initialize session routes with Redis client
    if redis_client:
        sessions.init_session_routes(redis_client)
        logger.info("Session routes initialized", component="routes")
    else:
        logger.warning("Session routes started without Redis (degraded)", component="routes")

    # Initialize task routes
    tasks.init_task_routes()
    logger.info("Task routes initialized", component="routes")

    # Initialize export routes with session manager
    session_mgr = sessions.get_session_manager()
    export.init_export_routes(session_mgr)
    logger.info("Export routes initialized", component="routes")

    # Initialize health routes with Redis client
    if redis_client:
        health.init_health_routes(redis_client)
        logger.info("Health routes initialized", component="routes")
    else:
        logger.warning("Health routes started without Redis (degraded)", component="routes")

    yield

    # Shutdown
    logger.info("Application shutting down", component="lifecycle")

    # Close Redis connection
    if redis_client:
        await redis_client.aclose()
        logger.info("Redis connection closed", component="redis")

    # Close database connections
    close_db()
    logger.info("Database connections closed", component="database")


def create_app(test_mode: bool = False) -> FastAPI:
    """
    Create and configure the FastAPI application.

    Args:
        test_mode: If True, disables authentication and CSRF middleware for testing

    Returns:
        FastAPI: Configured FastAPI application instance
    """
    app = FastAPI(
        title="APGI System API",
        version="1.0.0",
        description="""REST API for Allostatic Precision-Gated Ignition consciousness modeling.

## Rate Limiting

This API implements rate limiting to ensure fair usage and protect against abuse:

- **Default Rate Limit**: 60 requests per minute per client
- **Rate limiting is based on IP address** and applies to all endpoints
- **Rate limit headers** are included in all responses:
  - `X-RateLimit-Limit`: Maximum requests allowed per time window
  - `X-RateLimit-Remaining`: Remaining requests in current window
  - `X-RateLimit-Reset`: Time when the rate limit resets (Unix timestamp)

When the rate limit is exceeded, the API returns HTTP 429 (Too Many Requests) with details about when to retry.

## Authentication

All endpoints except `/health`, `/docs`, and `/openapi.json` require authentication using JWT tokens:
- Include `Authorization: Bearer <token>` in request headers
- Obtain tokens via the `/v1/auth/login` endpoint
- Refresh tokens using `/v1/auth/refresh` before expiration

## Data Formats

- All timestamps use ISO 8601 format with UTC timezone
- JSON is the primary data exchange format
- Binary data (exports) uses appropriate MIME types
""",
        docs_url="/docs" if settings.environment in ["development", "staging", "test"] else None,
        redoc_url=(
            "/redoc" if settings.environment in ["development", "staging", "test"] else None
        ),
        openapi_url=(
            "/openapi.json" if settings.environment in ["development", "staging", "test"] else None
        ),
        lifespan=lifespan,
    )
    app.state.test_mode = test_mode

    # Add request size limiting middleware (first, to catch large requests early)
    app.add_middleware(
        RequestSizeLimitMiddleware,
        max_size_mb=getattr(settings, "max_request_size_mb", 10),
        enabled=getattr(settings, "request_size_limit_enabled", True),
    )

    # Add GZip compression middleware
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    # Add metrics middleware (first, to track all requests)
    app.add_middleware(PrometheusMetricsMiddleware)

    # Add profiling middleware (if enabled)
    if getattr(settings, "profiling_enabled", False):
        app.add_middleware(
            ProfilingMiddleware,
            enabled=True,
            memory_tracing=getattr(settings, "profiling_memory_enabled", False),
            profile_functions=True,  # Enable detailed function profiling
        )

    # Add request logging middleware
    app.add_middleware(RequestLoggingMiddleware)

    # Add API versioning middleware (adds version headers to all responses)
    app.add_middleware(APIVersioningMiddleware)

    # Add response schema validation middleware - skip in test mode
    if not test_mode:
        app.add_middleware(
            ResponseSchemaValidationMiddleware,
            enabled=settings.schema_validation_enabled,
            fail_on_error=settings.schema_validation_fail_on_error,
        )

    # Add CSRF protection middleware - skip in test mode
    if not test_mode:
        app.add_middleware(
            CSRFMiddleware,
            enabled=(
                settings.csrf_protection_enabled
                if hasattr(settings, "csrf_protection_enabled")
                else True
            ),
            cookie_name="csrf_token",
            header_name="X-CSRF-Token",
            token_expiry_minutes=60,
        )

    # Add authentication middleware (extracts and verifies JWT tokens) - skip in test mode
    if not test_mode:
        app.add_middleware(AuthenticationMiddleware)

    # Add rate limiting middleware AFTER authentication in code (meaning it wraps it)
    # This ensures unauthenticated requests (e.g., login attempts) are rate-limited BEFORE auth processing
    if not test_mode:
        app.add_middleware(
            RateLimitingMiddleware, redis_client=None, enabled=settings.rate_limit_enabled
        )

    # Add security input validation middleware - skip in test mode
    if not test_mode:
        app.add_middleware(SecurityValidationMiddleware, enabled=True)

    # Add security headers middleware (Outer)
    app.add_middleware(SecurityHeadersMiddleware)

    # Add deprecation middleware (Outermost)
    app.add_middleware(
        DeprecationMiddleware, deprecated_endpoints=version.get_deprecated_endpoints()
    )

    # Configure CORS
    configure_cors(app)

    # Register exception handlers
    register_exception_handlers(app)

    # Root endpoint
    @app.get("/", tags=["Root"], response_model=RootResponse)
    async def root() -> Dict[str, str]:
        """API root endpoint with basic information."""
        return {
            "name": "APGI System API",
            "version": "1.0.0",
            "description": "REST API for consciousness modeling",
            "docs": "/docs",
            "health": "/health",
        }

    # Include routers
    app.include_router(admin.router)
    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(sessions.router)
    app.include_router(templates.router)
    app.include_router(state.router)
    app.include_router(tasks.router)
    app.include_router(export.router)
    app.include_router(metrics.router)
    app.include_router(health.router)
    app.include_router(version.router)
    app.include_router(payments.router)
    app.include_router(api_keys.router)
    app.include_router(webhooks.router)
    app.include_router(participants.router)
    app.include_router(studies.router)
    app.include_router(batteries.router)
    app.include_router(trials.router)
    app.include_router(norms.router)
    app.include_router(instrument.router)
    app.include_router(meta.router)
    app.include_router(longitudinal.router)
    app.include_router(nof1.router)

    # Mount static files for web UI
    web_dir = os.path.join(os.path.dirname(__file__), "..", "web")
    if os.path.exists(web_dir):
        app.mount("/web", StaticFiles(directory=web_dir), name="web")

    logger.info("APGI API application created successfully", version="1.0.0")

    return app


# Create application instance
app = create_app()


if __name__ == "__main__":
    import uvicorn

    default_port = 8000
    default_host = "127.0.0.1"  # Bind to localhost for security

    uvicorn.run("app.main:app", host=default_host, port=default_port, reload=True, log_level="info")


def cli() -> None:
    """CLI entry point for the APGI API."""
    from app.cli import cli as app_cli

    app_cli()

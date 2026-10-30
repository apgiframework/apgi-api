"""
Structured Logging Middleware

Provides structured JSON logging for all API requests and errors.
"""

import contextvars
import json
import logging
import time
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from starlette.types import ASGIApp

# Context variable for request ID propagation
request_id_context: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "request_id", default=None
)


class StructuredLogger:
    """
    Structured logger that outputs JSON-formatted log entries.
    """

    def __init__(self, name: str) -> None:
        self.logger = logging.getLogger(name)

    def _format_log_entry(self, level: str, message: str, **kwargs: Any) -> str:
        """
        Format log entry as JSON.

        Args:
            level: Log level (INFO, ERROR, etc.)
            message: Log message
            **kwargs: Additional fields to include in log entry

        Returns:
            JSON-formatted log string
        """
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
            "level": level,
            # Cloud Logging buckets entries by this exact key; without it every
            # entry lands at severity DEFAULT regardless of "level" above, which
            # breaks severity-based Cloud Monitoring alert policies and Error
            # Reporting's log-based detection on Cloud Run.
            "severity": level,
            "logger": self.logger.name,
            "message": message,
            **kwargs,
        }

        # Include request_id from context if available
        request_id = request_id_context.get()
        if request_id:
            log_entry["request_id"] = request_id

        from app.middleware.correlation import get_correlation_id

        correlation_id = get_correlation_id()
        if correlation_id:
            log_entry["correlation_id"] = correlation_id

        return json.dumps(log_entry)

    def info(self, message: str, **kwargs: Any) -> None:
        """Log info message with structured data."""
        try:
            # Don't pass kwargs to underlying logger - they're embedded in JSON message
            json_message = self._format_log_entry("INFO", message, **kwargs)
            self.logger.info(json_message)
        except Exception:  # nosec: B110
            pass

    def warning(self, message: str, **kwargs: Any) -> None:
        """Log warning message with structured data."""
        try:
            # Don't pass kwargs to underlying logger - they're embedded in JSON message
            json_message = self._format_log_entry("WARNING", message, **kwargs)
            self.logger.warning(json_message)
        except Exception:  # nosec: B110
            pass

    def error(self, message: str, **kwargs: Any) -> None:
        """Log error message with structured data."""
        try:
            # Don't pass kwargs to underlying logger - they're embedded in JSON message
            json_message = self._format_log_entry("ERROR", message, **kwargs)
            self.logger.error(json_message)
        except Exception:  # nosec: B110
            pass

    def debug(self, message: str, **kwargs: Any) -> None:
        """Log debug message with structured data."""
        try:
            # Don't pass kwargs to underlying logger - they're embedded in JSON message
            json_message = self._format_log_entry("DEBUG", message, **kwargs)
            self.logger.debug(json_message)
        except Exception:  # nosec: B110
            pass


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware that logs all HTTP requests with structured data.

    Logs include:
    - Request method, path, status code
    - Request duration
    - Client identifier
    - Request ID for tracing
    """

    def __init__(self, app: ASGIApp):
        super().__init__(app)
        self.logger = StructuredLogger("app.requests")

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """
        Process request and log details.

        Args:
            request: Incoming HTTP request
            call_next: Next middleware/handler in chain

        Returns:
            HTTP response
        """
        # Generate unique request ID
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id

        # Set request ID in context for downstream logging
        token = request_id_context.set(request_id)

        try:
            # Extract client identifier (IP or user ID if authenticated)
            client_id = request.client.host if request.client else "unknown"

            # Record start time
            start_time = time.time()

            # Process request
            try:
                response = await call_next(request)

                # Calculate duration
                duration_ms = (time.time() - start_time) * 1000

                # Log successful request
                self.logger.info(
                    "Request processed",
                    request_id=request_id,
                    method=request.method,
                    path=request.url.path,
                    status_code=response.status_code,
                    duration_ms=round(duration_ms, 2),
                    client_id=client_id,
                    user_agent=request.headers.get("user-agent", "unknown"),
                )

                # Add request ID to response headers
                response.headers["X-Request-ID"] = request_id

                return response

            except Exception as e:
                # Calculate duration
                duration_ms = (time.time() - start_time) * 1000

                # Log error
                self.logger.error(
                    "Request failed",
                    request_id=request_id,
                    method=request.method,
                    path=request.url.path,
                    duration_ms=round(duration_ms, 2),
                    client_id=client_id,
                    error_type=type(e).__name__,
                    error_message=str(e),
                    stack_trace=traceback.format_exc(),
                )

                # Re-raise exception to be handled by exception handlers
                raise
        finally:
            # Reset context variable
            request_id_context.reset(token)


class ErrorLoggingHandler:
    """
    Handler for logging errors with full context and stack traces.
    """

    def __init__(self) -> None:
        self.logger = StructuredLogger("app.errors")

    def log_error(
        self,
        error: Exception,
        request: Optional[Request] = None,
        error_code: Optional[str] = None,
        **context: Any,
    ) -> None:
        """
        Log error with full context.

        Args:
            error: Exception that occurred
            request: HTTP request (if available)
            error_code: Application error code
            **context: Additional context fields
        """
        try:
            log_data = {
                "error_type": type(error).__name__,
                "error_message": str(error),
                "stack_trace": traceback.format_exc(),
                **context,
            }

            # Add request context if available
            if request:
                log_data.update(
                    {
                        "request_id": getattr(request.state, "request_id", "unknown"),
                        "method": request.method,
                        "path": request.url.path,
                        "client_id": request.client.host if request.client else "unknown",
                    }
                )

            # Add error code if provided
            if error_code:
                log_data["error_code"] = error_code

            # Log the error with all context data as the structured message
            self.logger.error("Error occurred", **log_data)
        except Exception:  # nosec: B110
            # Silently fail if error logging fails - don't crash the application
            pass


# Global error logging handler instance
error_logger = ErrorLoggingHandler()


def configure_structured_logging(log_level: str = "INFO") -> None:
    """
    Configure Python logging to use structured format.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
    """
    import os

    # Disable uvicorn access logs (we handle them in middleware)
    # Do this before TEST_MODE check to ensure it's always executed
    logging.getLogger("uvicorn.access").disabled = True

    # Skip logging configuration in test mode to prevent LogRecord conflicts
    if os.environ.get("TEST_MODE") == "true":
        return

    # Only configure if not already configured (e.g., by pytest)
    root_logger = logging.getLogger()
    if not root_logger.handlers:  # pragma: no branch
        try:
            # Set root logger level
            logging.basicConfig(
                level=getattr(logging, log_level.upper()),
                format="%(message)s",  # Just output the message (already JSON formatted)
                handlers=[logging.StreamHandler()],
            )
        except Exception:  # pragma: no cover  # nosec: B110
            # If logging configuration fails, continue without it
            pass

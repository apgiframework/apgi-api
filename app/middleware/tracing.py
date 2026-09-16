"""
Distributed Tracing Configuration

Configures OpenTelemetry for distributed tracing across the application.
"""

import logging
from typing import Any, Optional

# Make OpenTelemetry imports conditional to handle compatibility issues
try:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.redis import RedisInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
    from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

    OPENTELEMETRY_AVAILABLE = True

    # Try to import JaegerExporter conditionally
    try:
        from opentelemetry.exporter.jaeger.thrift import JaegerExporter

        JAEGER_EXPORTER_AVAILABLE = True
    except ImportError as jaeger_error:  # pragma: no cover
        logger = logging.getLogger(__name__)
        logger.warning(f"JaegerExporter not available: {jaeger_error}")
        JAEGER_EXPORTER_AVAILABLE = False

except Exception as e:  # pragma: no cover
    logger = logging.getLogger(__name__)
    logger.warning(f"OpenTelemetry not available: {e}")
    OPENTELEMETRY_AVAILABLE = False
    JAEGER_EXPORTER_AVAILABLE = False

from app.config import settings

logger = logging.getLogger(__name__)


def configure_distributed_tracing(
    service_name: str = "apgi-api",
    service_version: str = "1.0.0",
    jaeger_endpoint: Optional[str] = None,
    otlp_endpoint: Optional[str] = None,
    enable_console_exporter: bool = False,
    sampling_rate: float = 1.0,
) -> None:
    """
    Configure distributed tracing with OpenTelemetry.

    Args:
        service_name: Name of the service
        service_version: Version of the service
        jaeger_endpoint: Jaeger collector endpoint (optional)
        otlp_endpoint: OTLP gRPC endpoint (optional)
        enable_console_exporter: Whether to export traces to console
        sampling_rate: Sampling rate for traces (0.0 to 1.0)
    """
    if not OPENTELEMETRY_AVAILABLE:
        logger.warning("OpenTelemetry not available, skipping tracing configuration")
        return

    try:
        # Set up resource
        resource = Resource.create(
            {
                SERVICE_NAME: service_name,
                SERVICE_VERSION: service_version,
            }
        )

        # Create tracer provider
        trace.set_tracer_provider(TracerProvider(resource=resource))

        # Add span processors based on configuration
        tracer_provider = trace.get_tracer_provider()

        # Console exporter for development
        if enable_console_exporter or False:
            console_processor = BatchSpanProcessor(ConsoleSpanExporter())
            tracer_provider.add_span_processor(console_processor)
            logger.info("Console trace exporter enabled")

        # Jaeger exporter
        if jaeger_endpoint and JAEGER_EXPORTER_AVAILABLE and JaegerExporter:
            jaeger_exporter = JaegerExporter(
                agent_host_name=(
                    jaeger_endpoint.split(":")[0] if ":" in jaeger_endpoint else jaeger_endpoint
                ),
                agent_port=int(jaeger_endpoint.split(":")[1]) if ":" in jaeger_endpoint else 6831,
            )
            jaeger_processor = BatchSpanProcessor(jaeger_exporter)
            tracer_provider.add_span_processor(jaeger_processor)
            logger.info(f"Jaeger trace exporter configured at {jaeger_endpoint}")
        elif jaeger_endpoint and not JAEGER_EXPORTER_AVAILABLE:
            logger.warning(
                "Jaeger endpoint configured but JaegerExporter not available, skipping Jaeger export"
            )

        # OTLP gRPC exporter
        if otlp_endpoint:
            otlp_exporter = OTLPSpanExporter(
                endpoint=otlp_endpoint,
                insecure=True,  # For development; use TLS in production
            )
            otlp_processor = BatchSpanProcessor(otlp_exporter)
            tracer_provider.add_span_processor(otlp_processor)
            logger.info(f"OTLP gRPC trace exporter configured at {otlp_endpoint}")

        # Instrument frameworks
        # FastAPI instrumentation
        FastAPIInstrumentor().instrument(
            server_request_hook=_server_request_hook,
            client_request_hook=_client_request_hook,
            client_response_hook=_client_response_hook,
        )

        # SQLAlchemy instrumentation
        try:
            SQLAlchemyInstrumentor().instrument()
        except Exception as e:
            logger.warning(f"SQLAlchemy instrumentation failed: {e}")

        # Redis instrumentation
        RedisInstrumentor().instrument()

        logger.info(
            f"Distributed tracing configured successfully for {service_name} v{service_version} with exporters: console={enable_console_exporter or False}, jaeger={bool(jaeger_endpoint)}, otlp={bool(otlp_endpoint)}, sampling_rate={sampling_rate}"
        )

    except Exception as e:
        logger.error(f"Failed to configure distributed tracing: {e}")
        raise


def _server_request_hook(span: Any, scope: Any) -> None:
    """Hook to add request-specific information to spans."""
    if span and span.is_recording():
        # Add route information
        if hasattr(scope, "get") and callable(scope.get):
            route = scope.get("route")
            if route:
                route_path = getattr(route, "path", None)
                span.set_attribute("http.route", route_path if route_path else str(route))

        # Add user information if available
        if hasattr(scope, "get") and callable(scope.get):
            user = scope.get("user")
            if user:
                span.set_attribute("user.id", getattr(user, "id", None))
                span.set_attribute("user.username", getattr(user, "username", None))


def _client_request_hook(span: Any, method: str, url: Any) -> None:
    """Hook for client requests."""
    if span and span.is_recording():
        span.set_attribute("http.method", method)
        span.set_attribute("http.url", str(url))


def _client_response_hook(span: Any, response_headers: Any, response_body: Any) -> None:
    """Hook for client responses."""
    if span and span.is_recording():
        span.set_attribute("http.response_complete", True)


def instrument_application() -> None:
    """
    Instrument the FastAPI application with OpenTelemetry.

    This is called after the FastAPI app is fully configured.
    """
    if not OPENTELEMETRY_AVAILABLE:
        logger.warning("OpenTelemetry not available, skipping application instrumentation")
        return

    try:
        # Configure tracing based on settings
        configure_distributed_tracing(
            service_name=getattr(settings, "tracing_service_name", "apgi-api"),
            service_version=getattr(settings, "tracing_service_version", "1.0.0"),
            jaeger_endpoint=getattr(settings, "jaeger_endpoint", None),
            otlp_endpoint=getattr(settings, "otlp_endpoint", None),
            enable_console_exporter=getattr(settings, "tracing_console_exporter", False),
            sampling_rate=getattr(settings, "tracing_sampling_rate", 1.0),
        )

        logger.info("Application instrumentation completed")

    except ImportError:
        logger.warning("OpenTelemetry not available, skipping instrumentation")
    except Exception as e:
        logger.error(f"Failed to instrument application: {e}")

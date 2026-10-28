"""
Metrics Routes

Endpoints for exposing Prometheus metrics and business dashboard metrics.
"""

import html
import json
import logging
import os
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from starlette.responses import Response as StarletteResponse

from app.middleware.metrics import get_metrics_response
from app.services.authorization import Permission, require_permission
from app.services.business_metrics import BusinessMetricsService
from app.services.cache_service import get_cache_service
from app.services.profiling_service import ProfilingService

router = APIRouter(prefix="/v1", tags=["Metrics"])
# Development-only profiling routes; not mounted in production (see app.main).
dev_router = APIRouter(prefix="/v1", tags=["Metrics"])

logger = logging.getLogger(__name__)

# Business metrics service instance
_business_metrics_service: Optional[BusinessMetricsService] = None

# Profiling service instance
_profiling_service: Optional[ProfilingService] = None


def get_business_metrics_service() -> BusinessMetricsService:
    """Get BusinessMetricsService dependency."""
    global _business_metrics_service
    if _business_metrics_service is None:
        _business_metrics_service = BusinessMetricsService()
    return _business_metrics_service


def get_profiling_service() -> ProfilingService:
    """Get ProfilingService dependency."""
    global _profiling_service
    if _profiling_service is None:
        _profiling_service = ProfilingService()
    return _profiling_service


@router.get("/metrics")
async def metrics_endpoint() -> StarletteResponse:
    """
    Prometheus metrics endpoint.

    Returns metrics in Prometheus text format for scraping.

    Returns:
        Prometheus metrics
    """
    return get_metrics_response()


@router.get(
    "/dashboard/overview",
    summary="Get dashboard overview metrics",
    description="Retrieve high-level overview metrics for the business dashboard",
    dependencies=[Depends(require_permission(Permission.DATA_READ))],
)
async def get_dashboard_overview(
    service: BusinessMetricsService = Depends(get_business_metrics_service),
) -> dict[str, Any]:
    """
    Get dashboard overview metrics.

    Returns:
        Overview metrics for dashboard display
    """
    try:
        return await service.get_overview_metrics()
    except Exception as e:
        logger.exception("Failed to get dashboard overview")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred",
        )


@router.get(
    "/dashboard/sessions",
    summary="Get session metrics",
    description="Retrieve session-related metrics for the business dashboard",
    dependencies=[Depends(require_permission(Permission.DATA_READ))],
)
async def get_dashboard_sessions(
    days: int = 30, service: BusinessMetricsService = Depends(get_business_metrics_service)
) -> dict[str, Any]:
    """
    Get session metrics for dashboard.

    Args:
        days: Number of days to look back for metrics

    Returns:
        Session metrics for dashboard display
    """
    try:
        if days < 1 or days > 365:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Days must be between 1 and 365"
            )
        return service.get_session_metrics(days)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to get session metrics")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred",
        )


@router.get(
    "/dashboard/tasks",
    summary="Get task metrics",
    description="Retrieve task-related metrics for the business dashboard",
    dependencies=[Depends(require_permission(Permission.DATA_READ))],
)
async def get_dashboard_tasks(
    days: int = 30, service: BusinessMetricsService = Depends(get_business_metrics_service)
) -> dict[str, Any]:
    """
    Get task metrics for dashboard.

    Args:
        days: Number of days to look back for metrics

    Returns:
        Task metrics for dashboard display
    """
    try:
        if days < 1 or days > 365:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Days must be between 1 and 365"
            )
        return service.get_task_metrics(days)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to get task metrics")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred",
        )


@router.get(
    "/dashboard/users",
    summary="Get user metrics",
    description="Retrieve user-related metrics for the business dashboard",
    dependencies=[Depends(require_permission(Permission.DATA_READ))],
)
async def get_dashboard_users(
    days: int = 30, service: BusinessMetricsService = Depends(get_business_metrics_service)
) -> dict[str, Any]:
    """
    Get user metrics for dashboard.

    Args:
        days: Number of days to look back for metrics

    Returns:
        User metrics for dashboard display
    """
    try:
        if days < 1 or days > 365:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Days must be between 1 and 365"
            )
        return service.get_user_metrics(days)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to get user metrics")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred",
        )


@router.get(
    "/dashboard/templates",
    summary="Get template metrics",
    description="Retrieve template-related metrics for the business dashboard",
    dependencies=[Depends(require_permission(Permission.DATA_READ))],
)
async def get_dashboard_templates(
    days: int = 30, service: BusinessMetricsService = Depends(get_business_metrics_service)
) -> dict[str, Any]:
    """
    Get template metrics for dashboard.

    Args:
        days: Number of days to look back for metrics

    Returns:
        Template metrics for dashboard display
    """
    try:
        if days < 1 or days > 365:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Days must be between 1 and 365"
            )
        return service.get_template_metrics(days)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to get template metrics")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred",
        )


@router.get(
    "/dashboard",
    summary="Get complete dashboard data",
    description="Retrieve complete business metrics dashboard data",
    dependencies=[Depends(require_permission(Permission.DATA_READ))],
)
async def get_complete_dashboard(
    days: int = 30, service: BusinessMetricsService = Depends(get_business_metrics_service)
) -> dict[str, Any]:
    """
    Get complete dashboard data.

    Args:
        days: Number of days to look back for metrics

    Returns:
        Complete dashboard data with all metrics
    """
    try:
        if days < 1 or days > 365:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Days must be between 1 and 365"
            )

        # Check cache first
        cache_service = get_cache_service()
        if cache_service:
            cache_key = f"dashboard:{days}"
            cached_data = await cache_service.get_api_response("/dashboard", cache_key)
            if cached_data:
                return cached_data

        # Compute dashboard data
        dashboard_data = await service.get_dashboard_data(days)

        # Cache the result for 15 minutes (900 seconds)
        if cache_service:
            await cache_service.set_api_response("/dashboard", cache_key, dashboard_data, 900)

        return dashboard_data
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to get dashboard data")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred",
        )


def _load_dashboard_template() -> str:
    """Load the dashboard HTML template from file."""
    # Template decoupled to web/ directory for frontend separation
    template_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "web", "dashboard.html"
    )
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        logger.error(f"Dashboard template not found at {template_path}")
        raise


def _render_dashboard_template(template: str, days: int, dashboard_data: dict[str, Any]) -> str:
    """Render the dashboard template with data."""
    overview = dashboard_data.get("overview", {})
    system = dashboard_data.get("system", {})

    # Safely escape all values for HTML insertion
    return (
        template.replace("{{days}}", html.escape(str(days)))
        .replace("{{total_requests}}", html.escape(str(overview.get("total_requests", 0))))
        .replace("{{active_users}}", html.escape(str(overview.get("active_users", 0))))
        .replace(
            "{{avg_response_time}}",
            html.escape(f"{overview.get('avg_response_time', 0):.2f}"),
        )
        .replace("{{error_rate}}", html.escape(f"{overview.get('error_rate', 0):.2f}"))
        .replace("{{uptime_hours}}", html.escape(f"{system.get('uptime_hours', 0):.1f}"))
        .replace("{{cpu_usage}}", html.escape(f"{system.get('cpu_usage', 0):.1f}"))
        .replace("{{memory_usage}}", html.escape(f"{system.get('memory_usage', 0):.1f}"))
        .replace("{{dashboard_data_json}}", json.dumps(dashboard_data))
    )


@router.get(
    "/dashboard/html",
    summary="Dashboard HTML Interface",
    description="Real-time API analytics dashboard interface",
    response_class=HTMLResponse,
    dependencies=[Depends(require_permission(Permission.DATA_READ))],
)
async def get_dashboard_html(
    days: int = 30, service: BusinessMetricsService = Depends(get_business_metrics_service)
) -> HTMLResponse:
    """
    Get HTML dashboard interface for API analytics.

    Args:
        days: Number of days to look back for metrics

    Returns:
        HTML dashboard page
    """
    try:
        if days < 1 or days > 365:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Days must be between 1 and 365"
            )

        # Get dashboard data
        dashboard_data = await service.get_dashboard_data(days)

        # Load and render template
        template = _load_dashboard_template()
        html_content = _render_dashboard_template(template, days, dashboard_data)

        return HTMLResponse(content=html_content)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to generate dashboard")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred",
        )


@dev_router.post(
    "/profiling/memory/start",
    summary="Start memory tracing",
    description="Start collecting detailed memory usage information",
    dependencies=[Depends(require_permission(Permission.SYSTEM_ADMIN))],
)
async def start_memory_tracing(
    service: ProfilingService = Depends(get_profiling_service),
) -> dict[str, str]:
    """
    Start memory tracing for performance analysis.

    Returns:
        Status message
    """
    try:
        service.start_memory_tracing()
        return {"message": "Memory tracing started successfully"}
    except Exception as e:
        logger.exception("Failed to start memory tracing")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred",
        )


@dev_router.post(
    "/profiling/memory/stop",
    summary="Stop memory tracing",
    description="Stop collecting detailed memory usage information",
    dependencies=[Depends(require_permission(Permission.SYSTEM_ADMIN))],
)
async def stop_memory_tracing(
    service: ProfilingService = Depends(get_profiling_service),
) -> dict[str, str]:
    """
    Stop memory tracing.

    Returns:
        Status message
    """
    try:
        service.stop_memory_tracing()
        return {"message": "Memory tracing stopped successfully"}
    except Exception as e:
        logger.exception("Failed to stop memory tracing")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred",
        )


@dev_router.get(
    "/profiling/memory",
    summary="Get memory usage snapshot",
    description="Retrieve current memory usage and allocation details",
    dependencies=[Depends(require_permission(Permission.DATA_READ))],
)
async def get_memory_snapshot(service: ProfilingService = Depends(get_profiling_service)) -> Any:
    """
    Get current memory usage snapshot.

    Returns:
        Memory usage statistics
    """
    try:
        return service.get_memory_snapshot()
    except Exception as e:
        logger.exception("Failed to get memory snapshot")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred",
        )


@dev_router.get(
    "/profiling/system",
    summary="Get system performance metrics",
    description="Retrieve current system performance metrics (CPU, memory, threads)",
    dependencies=[Depends(require_permission(Permission.DATA_READ))],
)
async def get_system_performance(service: ProfilingService = Depends(get_profiling_service)) -> Any:
    """
    Get current system performance metrics.

    Returns:
        System performance data
    """
    try:
        return service.get_system_performance()
    except Exception as e:
        logger.exception("Failed to get system performance")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred",
        )


@dev_router.get(
    "/profiling/history",
    summary="Get performance history",
    description="Retrieve historical performance metrics for analysis",
    dependencies=[Depends(require_permission(Permission.DATA_READ))],
)
async def get_performance_history(
    hours: int = 1, service: ProfilingService = Depends(get_profiling_service)
) -> Any:
    """
    Get performance history for the specified time period.

    Args:
        hours: Number of hours to look back

    Returns:
        Historical performance data
    """
    try:
        if hours < 1 or hours > 24:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Hours must be between 1 and 24"
            )
        return service.get_performance_history(hours)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to get performance history")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred",
        )


@dev_router.get(
    "/profiling/analysis",
    summary="Get bottleneck analysis",
    description="Analyze performance data to identify bottlenecks and recommendations",
    dependencies=[Depends(require_permission(Permission.DATA_READ))],
)
async def get_bottleneck_analysis(
    service: ProfilingService = Depends(get_profiling_service),
) -> Any:
    """
    Get performance bottleneck analysis.

    Returns:
        Bottleneck analysis and recommendations
    """
    try:
        return service.get_bottleneck_analysis()
    except Exception as e:
        logger.exception("Failed to get bottleneck analysis")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred",
        )

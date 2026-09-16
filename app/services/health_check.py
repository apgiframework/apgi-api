"""
Health Check Service

Provides health check functionality for monitoring API dependencies.
"""

import time
from datetime import datetime, timezone
from typing import Any, Dict

import redis.asyncio as redis
from sqlalchemy import text

from app.celery_app import celery_app
from app.config import settings


class HealthCheckService:
    """Service for performing health checks on API dependencies."""

    def __init__(self, redis_client: redis.Redis):
        """
        Initialize health check service.

        Args:
            redis_client: Redis client instance
        """
        self.redis_client = redis_client

    async def perform_readiness_check(self) -> Dict[str, Any]:
        """
        Perform readiness check on critical dependencies only.

        Readiness checks should be lightweight and only verify that the service
        can accept traffic. This excludes optional components like Celery workers.

        Returns:
            Dict containing readiness status and critical dependency information
        """
        dependencies = {}
        all_ready = True

        # Check Redis connectivity (critical for session management)
        try:
            start_time = time.time()
            redis_result = await self.redis_client.ping()
            redis_time = time.time() - start_time

            # Performance threshold (in seconds)
            redis_threshold = settings.health_connectivity_threshold

            if redis_time > redis_threshold:
                dependencies["redis"] = {
                    "status": "degraded",
                    "message": f"Connected but slow: {redis_time:.3f}s (threshold: {redis_threshold}s)",
                }
                all_ready = False
            else:
                dependencies["redis"] = {
                    "status": "ready",
                    "message": "Connected and responsive",
                    "response_time": f"{redis_time:.3f}s",
                }
        except Exception as e:
            dependencies["redis"] = {"status": "not_ready", "message": str(e)}
            all_ready = False

        # Check database connectivity (critical for user data)
        try:
            from app.database.connection import engine

            # Test basic connectivity only (no performance checks)
            start_time = time.time()
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            connectivity_time = time.time() - start_time

            # Performance threshold (in seconds)
            connectivity_threshold = 0.1  # 100ms

            if connectivity_time > connectivity_threshold:
                dependencies["database"] = {
                    "status": "degraded",
                    "message": f"Connected but slow: {connectivity_time:.3f}s (threshold: {connectivity_threshold}s)",
                }
                all_ready = False
            else:
                dependencies["database"] = {
                    "status": "ready",
                    "message": "Connected",
                    "connectivity_time": f"{connectivity_time:.3f}s",
                }
        except Exception as e:
            dependencies["database"] = {"status": "not_ready", "message": str(e)}
            all_ready = False

        return {
            "status": "ready" if all_ready else "not_ready",
            "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
            "dependencies": dependencies,
        }

    async def perform_health_check(self) -> Dict[str, Any]:
        """
        Perform comprehensive health check on all dependencies.

        Returns:
            Dict containing health status and dependency information
        """
        from app.config import settings

        dependencies = {}
        all_healthy = True
        critical_services = settings.health_critical_services

        # Check Redis connectivity and performance
        try:
            start_time = time.time()
            redis_result = await self.redis_client.ping()
            redis_time = time.time() - start_time

            # Performance threshold (in seconds)
            redis_threshold = settings.health_connectivity_threshold

            if redis_time > redis_threshold:
                dependencies["redis"] = {
                    "status": "degraded",
                    "message": f"Connected but slow: {redis_time:.3f}s (threshold: {redis_threshold}s)",
                }
                all_healthy = False
            else:
                dependencies["redis"] = {
                    "status": "healthy",
                    "message": "Connected and responsive",
                    "response_time": f"{redis_time:.3f}s",
                }
        except Exception as e:
            dependencies["redis"] = {"status": "unhealthy", "message": f"CRITICAL: {str(e)}"}
            if "redis" in critical_services:  # pragma: no branch
                all_healthy = False

        # Check database connectivity and performance
        try:
            from app.database.connection import engine

            # Test basic connectivity
            start_time = time.time()
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            connectivity_time = time.time() - start_time

            # Test query performance with a simpler query
            start_time = time.time()
            with engine.connect() as conn:
                # Test a simple query that works across databases
                result = conn.execute(text("SELECT COUNT(*) FROM (SELECT 1) AS dummy"))
                row = result.fetchone()
                if row is not None:
                    dummy_count = row[0]
                else:  # pragma: no cover
                    dummy_count = 0
            query_time = time.time() - start_time

            # Performance thresholds (in seconds)
            from app.config import settings

            connectivity_threshold = settings.health_connectivity_threshold  # 100ms default
            query_threshold = settings.health_query_threshold  # 500ms default

            # Check performance
            performance_issues = []
            if connectivity_time > connectivity_threshold:
                performance_issues.append(
                    f"Connectivity slow: {connectivity_time:.3f}s (threshold: {connectivity_threshold}s)"
                )
            if query_time > query_threshold:
                performance_issues.append(
                    f"Query slow: {query_time:.3f}s (threshold: {query_threshold}s)"
                )

            if performance_issues:
                dependencies["database"] = {
                    "status": "degraded",
                    "message": f"Connected but performance issues: {'; '.join(performance_issues)}",
                    "connectivity_time": f"{connectivity_time:.3f}s",
                    "query_time": f"{query_time:.3f}s",
                }
                all_healthy = False
            else:
                dependencies["database"] = {
                    "status": "healthy",
                    "message": "Connected",
                }
        except Exception as e:
            dependencies["database"] = {"status": "unhealthy", "message": f"CRITICAL: {str(e)}"}
            if "database" in critical_services:  # pragma: no branch
                all_healthy = False

        # Check Celery worker status
        try:
            # First, try to ping the workers
            ping_result = celery_app.control.ping(timeout=1.0)

            if ping_result:
                # Workers are responding to ping
                worker_count = len(ping_result)

                # Get additional stats
                inspect = celery_app.control.inspect()
                active_tasks = inspect.active()
                stats = inspect.stats()

                task_count = 0
                if active_tasks:
                    task_count = sum(len(tasks) for tasks in active_tasks.values())

                dependencies["celery"] = {
                    "status": "healthy",
                    "message": f"Workers responding: {worker_count}, tasks running: {task_count}",
                    "active_workers": str(worker_count),
                    "running_tasks": str(task_count),
                }
            else:
                dependencies["celery"] = {
                    "status": "unhealthy",
                    "message": "OPTIONAL: No Celery workers responded to ping",
                }
                # Celery is optional, do not fail overall health
        except Exception as e:
            dependencies["celery"] = {
                "status": "unknown",
                "message": "OPTIONAL: Not checked",
            }

        return {
            "status": "healthy" if all_healthy else "unhealthy",
            "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
            "dependencies": dependencies,
        }

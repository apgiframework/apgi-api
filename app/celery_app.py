"""
Celery Application Configuration

Configures Celery for asynchronous task execution with Redis broker.
"""

from celery import Celery

from app.config import settings

# Validate broker and backend URLs


def _validate_celery_urls() -> None:
    """Validate Celery broker and backend URLs."""
    from urllib.parse import urlparse

    for url_name, url_value in [
        ("broker", settings.celery_broker_url),
        ("backend", settings.celery_result_backend),
    ]:
        if not url_value:
            raise ValueError(f"Celery {url_name} URL is not configured")

        parsed = urlparse(url_value)
        if parsed.scheme not in ["redis", "redis+socket"]:
            raise ValueError(f"Celery {url_name} URL must use redis scheme, got: {parsed.scheme}")

        if not parsed.hostname and not parsed.path:
            raise ValueError(f"Celery {url_name} URL is invalid: {url_value}")


_validate_celery_urls()

# Create Celery app
celery_app = Celery(
    "apgi_tasks",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.tasks.experimental_tasks",
        "app.tasks.webhook_tasks",
        "app.tasks.acquisition_tasks",
        "app.tasks.ops_tasks",
        "app.tasks.scoring_tasks",
        "app.tasks.deletion_tasks",
    ],
)

# Configure Celery
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,  # 1 hour max per task
    task_soft_time_limit=3300,  # 55 minutes soft limit
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=50,
    result_expires=86400,  # Results expire after 24 hours
)

# Task routing (optional - for future expansion)
celery_app.conf.task_routes = {
    "app.tasks.experimental_tasks.*": {"queue": "experimental"},
    "app.tasks.webhook_tasks.*": {"queue": "celery"},
}

# Beat schedule for periodic tasks
celery_app.conf.beat_schedule = {
    "retry-failed-webhooks": {
        "task": "process_pending_webhooks",
        "schedule": 60.0,  # Run every minute
    },
    "ops-evaluate-alerts": {
        "task": "ops.evaluate_alerts",
        "schedule": 300.0,
    },
    "ops-sweep-jobs": {
        "task": "ops.sweep_jobs",
        "schedule": 60.0,
    },
}


def get_queue_depth(queue_name: str = "celery") -> int:
    """
    Get the number of pending tasks in a specific queue.

    Args:
        queue_name: Name of the queue to check

    Returns:
        Number of pending tasks
    """
    try:
        with celery_app.pool.acquire(block=True) as conn:
            return int(conn.default_channel.client.llen(queue_name))
    except Exception:
        return 0

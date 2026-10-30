"""
Platform-health readings for the console's panel 6 (S2-OPS-02).

Read-only. Local probes (database, Redis, Celery workers) always run. Cloud
Monitoring request metrics and Cloud SQL backup runs are read over REST with
the service's own credentials, and only when ``GCP_PROJECT_ID`` plus
``CLOUD_RUN_SERVICE`` / ``CLOUD_SQL_INSTANCE`` are set. Anything that cannot be
read is ``None`` (shown as unknown), with the failing source named in
``errors``; no value is ever estimated. Results are cached briefly to respect
API quotas, and each source reports when it was actually read.
"""

from __future__ import annotations

import concurrent.futures
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Protocol

from app.config import settings

logger = logging.getLogger(__name__)

WINDOW = timedelta(hours=1)
HTTP_TIMEOUT_S = 3.0
SCOPES = ["https://www.googleapis.com/auth/cloud-platform.read-only"]
MONITORING = "https://monitoring.googleapis.com/v3/projects/{project}/timeSeries"
SQLADMIN = "https://sqladmin.googleapis.com/v1/projects/{project}/instances/{instance}/backupRuns"


class Fetcher(Protocol):
    def __call__(self, url: str, params: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError


def _google_fetcher() -> Fetcher:
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    credentials, _ = google.auth.default(scopes=SCOPES)
    session = AuthorizedSession(credentials)

    def fetch(url: str, params: Dict[str, Any]) -> Dict[str, Any]:
        resp = session.get(url, params=params, timeout=HTTP_TIMEOUT_S)
        resp.raise_for_status()
        data: Dict[str, Any] = resp.json()
        return data

    return fetch


def _iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Local probes
# ---------------------------------------------------------------------------


def probe_database() -> Optional[float]:
    from sqlalchemy import text

    from app.database.connection import engine

    start = time.perf_counter()
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return round((time.perf_counter() - start) * 1000, 1)


def probe_workers() -> Optional[int]:
    from app.celery_app import celery_app

    replies = celery_app.control.inspect(timeout=1.0).ping()
    return len(replies or {})


# ---------------------------------------------------------------------------
# Cloud readings
# ---------------------------------------------------------------------------


def _service_filter(metric: str, service: str) -> str:
    return (
        f'metric.type="run.googleapis.com/{metric}" '
        f'AND resource.type="cloud_run_revision" '
        f'AND resource.labels.service_name="{service}"'
    )


def _point_value(point: Dict[str, Any]) -> float:
    v = point.get("value", {})
    if "doubleValue" in v:
        return float(v["doubleValue"])
    if "int64Value" in v:
        return float(v["int64Value"])
    raise ValueError("unexpected point value type")


def read_request_metrics(
    fetch: Fetcher, project: str, service: str, now: datetime
) -> Dict[str, Any]:
    """Success ratio (non-5xx) and worst-instance p95 latency over the last hour."""
    interval = {
        "interval.startTime": _iso(now - WINDOW),
        "interval.endTime": _iso(now),
    }
    url = MONITORING.format(project=project)

    counts = fetch(
        url,
        {
            "filter": _service_filter("request_count", service),
            **interval,
            "aggregation.alignmentPeriod": f"{int(WINDOW.total_seconds())}s",
            "aggregation.perSeriesAligner": "ALIGN_SUM",
            "aggregation.crossSeriesReducer": "REDUCE_SUM",
            "aggregation.groupByFields": "metric.labels.response_code_class",
        },
    )
    total = 0.0
    errors = 0.0
    for series in counts.get("timeSeries", []):
        n = sum(_point_value(p) for p in series.get("points", []))
        total += n
        if series.get("metric", {}).get("labels", {}).get("response_code_class") == "5xx":
            errors += n

    latency = fetch(
        url,
        {
            "filter": _service_filter("request_latencies", service),
            **interval,
            "aggregation.alignmentPeriod": f"{int(WINDOW.total_seconds())}s",
            "aggregation.perSeriesAligner": "ALIGN_PERCENTILE_95",
            "aggregation.crossSeriesReducer": "REDUCE_MAX",
        },
    )
    p95 = None
    for series in latency.get("timeSeries", []):
        for p in series.get("points", []):
            val = _point_value(p)
            p95 = val if p95 is None else max(p95, val)

    return {
        "request_count_1h": int(total),
        "error_ratio_5xx": round(errors / total, 5) if total else None,
        "success_ratio": round(1 - errors / total, 5) if total else None,
        "p95_latency_ms": round(p95, 1) if p95 is not None else None,
    }


def read_last_backup(fetch: Fetcher, project: str, instance: str) -> Optional[str]:
    data = fetch(SQLADMIN.format(project=project, instance=instance), {"maxResults": 10})
    ends = [
        r["endTime"]
        for r in data.get("items", [])
        if r.get("status") == "SUCCESSFUL" and r.get("endTime")
    ]
    return max(ends) if ends else None


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

_cache_lock = threading.Lock()
_cache: Dict[str, Any] = {"at": 0.0, "value": None}


_probe_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="ph-probe")


def _attempt(name: str, errors: List[str], fn: Callable[[], Any], hard_timeout: float = 4.0) -> Any:
    """Run ``fn`` with a hard time limit so a hung broker or API cannot stall the console."""
    try:
        return _probe_pool.submit(fn).result(timeout=hard_timeout)
    except Exception as exc:
        # Type only: messages from auth/HTTP libraries can echo URLs or tokens.
        logger.warning("platform health source %s failed: %s", name, type(exc).__name__)
        errors.append(f"{name}: {type(exc).__name__}")
        return None


def build_snapshot(
    fetch_factory: Callable[[], Fetcher] = _google_fetcher,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    errors: List[str] = []
    project = settings.gcp_project_id
    service = settings.cloud_run_service
    instance = settings.cloud_sql_instance

    db_ms = _attempt("database", errors, probe_database)
    workers = _attempt("celery", errors, probe_workers)

    metrics: Optional[Dict[str, Any]] = None
    last_backup: Optional[str] = None
    as_of: Dict[str, Optional[str]] = {"local_probes": now.isoformat()}
    needs_cloud = (project and service) or (project and instance)
    fetch = _attempt("google_auth", errors, fetch_factory) if needs_cloud else None
    if fetch is not None and project and service:
        metrics = _attempt(
            "cloud_monitoring", errors, lambda: read_request_metrics(fetch, project, service, now)
        )
        as_of["cloud_monitoring"] = now.isoformat() if metrics is not None else None
    if fetch is not None and project and instance:
        last_backup = _attempt(
            "cloud_sql_backups", errors, lambda: read_last_backup(fetch, project, instance)
        )
        as_of["cloud_sql_backups"] = now.isoformat() if last_backup is not None else None

    configured = {
        "cloud_monitoring": bool(project and service),
        "cloud_sql_backups": bool(project and instance),
    }
    notes = []
    if not configured["cloud_monitoring"]:
        notes.append("Request metrics need GCP_PROJECT_ID and CLOUD_RUN_SERVICE.")
    if not configured["cloud_sql_backups"]:
        notes.append("Backup status needs GCP_PROJECT_ID and CLOUD_SQL_INSTANCE.")
    notes.append(
        "Restore-drill dates need the deletion/recovery record (S2-DEL-03.3), not yet built."
    )

    m = metrics or {}
    return {
        "success_ratio_1h": m.get("success_ratio"),
        "error_ratio_5xx": m.get("error_ratio_5xx"),
        "p95_latency_ms": m.get("p95_latency_ms"),
        "request_count_1h": m.get("request_count_1h"),
        "last_backup_at": last_backup,
        "last_restore_drill_at": None,
        "database_latency_ms": db_ms,
        "celery_workers_online": workers,
        "configured": configured,
        "errors": errors,
        "source": " ".join(notes),
        "as_of": as_of,
    }


def snapshot(force: bool = False) -> Dict[str, Any]:
    """Cached snapshot; at most one live read per ``platform_health_ttl_s``."""
    with _cache_lock:
        fresh = time.monotonic() - _cache["at"] < settings.platform_health_ttl_s
        if not force and fresh and _cache["value"] is not None:
            value: Dict[str, Any] = _cache["value"]
            return value
        value = build_snapshot()
        _cache.update(at=time.monotonic(), value=value)
        return value


def reset_cache() -> None:
    with _cache_lock:
        _cache.update(at=0.0, value=None)

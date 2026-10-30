# mypy: disable-error-code="assignment"
"""
Operational alerting for the console (S2-OPS-03).

Rules read the same state the dashboard shows. Each finding has a stable
dedup key, so a standing condition raises one alert, not one per cycle.
Lifecycle: open -> (acknowledged) -> resolved; resolution sends a recovery
notice. Delivery reuses the notification channels in app.middleware.alerting.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.database.models import AcquisitionUpload, OpsAlert, OpsJob, Task, TaskStatus
from app.middleware.alerting import (
    Alert,
    AlertSeverity,
    EmailNotificationChannel,
    LogNotificationChannel,
    NotificationChannel,
    WebhookNotificationChannel,
)
from app.services import job_tracker, ops_monitor

logger = logging.getLogger(__name__)

LIVE = ("open", "acknowledged")


@dataclass(frozen=True)
class Finding:
    key: str
    rule: str
    severity: str
    title: str
    detail: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def evaluate_rules(db: Session) -> List[Finding]:
    now = _now()
    out: List[Finding] = []

    for f in ops_monitor.freshness(db):
        if f["state"] == "stale":
            out.append(
                Finding(
                    f"stale_source:{f['source']}",
                    "stale_source",
                    "warning",
                    f"Source stale: {f['source']}",
                    f"No new data for {ops_monitor.dur_text(f['age_seconds'])} "
                    f"(expected every {ops_monitor.dur_text(f['expected_interval_seconds'])}).",
                )
            )

    for job in db.query(OpsJob).filter(OpsJob.status.in_(("queued", "running", "failed", "dead"))):
        state = job_tracker.classify(job, now)
        label = f"{job.kind}:{job.ref}"
        cid = f" correlation_id={job.correlation_id}" if job.correlation_id else ""
        if state == "dead":
            out.append(
                Finding(
                    f"dead_job:{job.job_id}",
                    "dead_job",
                    "error",
                    f"Job dead-lettered: {label}",
                    f"Retries exhausted. {job.error or ''}{cid}",
                )
            )
        elif state in ("lease_expired", "stale_queued"):
            out.append(
                Finding(
                    f"{state}:{job.job_id}",
                    state,
                    "warning",
                    f"Job {'lost' if state == 'lease_expired' else 'never started'}: {label}",
                    f"State {state}.{cid}",
                )
            )

    since = now - timedelta(hours=24)
    quarantined = (
        db.query(func.count(AcquisitionUpload.upload_id))
        .filter(
            AcquisitionUpload.status.in_(("quarantined", "rejected")),
            AcquisitionUpload.updated_at >= since,
        )
        .scalar()
        or 0
    )
    if quarantined >= settings.alert_quarantine_threshold:
        out.append(
            Finding(
                "quarantine_rate",
                "quarantine_rate",
                "warning",
                "Quarantined or rejected chunks above threshold",
                f"{quarantined} in 24h (threshold {settings.alert_quarantine_threshold}).",
            )
        )

    failed_tasks = (
        db.query(func.count(Task.task_id))
        .filter(Task.status == TaskStatus.FAILED, Task.completed_at >= now - timedelta(hours=1))
        .scalar()
        or 0
    )
    if failed_tasks >= settings.alert_failed_task_threshold:
        out.append(
            Finding(
                "task_failures",
                "task_failures",
                "error",
                "Task failures above threshold",
                f"{failed_tasks} failed in the last hour (threshold "
                f"{settings.alert_failed_task_threshold}).",
            )
        )
    return out


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


def build_channels() -> List[NotificationChannel]:
    channels: List[NotificationChannel] = []
    for url in settings.alert_webhook_urls:
        channels.append(WebhookNotificationChannel(url))
    if settings.smtp_server and settings.alert_email_recipients:
        channels.append(
            EmailNotificationChannel(
                smtp_server=settings.smtp_server,
                smtp_port=settings.smtp_port,
                smtp_username=settings.smtp_username or "",
                smtp_password=settings.smtp_password or "",
                from_email=settings.smtp_from_email,
                to_emails=list(settings.alert_email_recipients),
            )
        )
    if settings.alert_enable_log_channel:
        channels.append(LogNotificationChannel())
    return channels


async def _send(channels: List[NotificationChannel], alert: Alert) -> bool:
    results = await asyncio.gather(*(c.send_alert(alert) for c in channels), return_exceptions=True)
    return any(r is True for r in results)


def notify(title: str, message: str, severity: str, meta: Dict[str, Any]) -> bool:
    """Send via every channel; True if at least one accepted it."""
    channels = build_channels()
    if not channels:
        logger.warning("no alert channels configured; alert not delivered: %s", title)
        return False
    alert = Alert(title=title, message=message, severity=AlertSeverity(severity), metadata=meta)
    try:
        return asyncio.run(_send(channels, alert))
    except Exception:
        logger.exception("alert delivery failed")
        return False


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


def run_cycle(db: Session, now: Optional[datetime] = None) -> Dict[str, int]:
    """Evaluate rules, open/refresh/resolve alerts, and notify. Idempotent per cycle."""
    now = now or _now()
    stats = {"opened": 0, "resolved": 0, "reminded": 0, "unchanged": 0}
    findings = {f.key: f for f in evaluate_rules(db)}
    live: Dict[Any, OpsAlert] = {
        a.dedup_key: a for a in db.query(OpsAlert).filter(OpsAlert.status.in_(LIVE))
    }
    reminder = timedelta(hours=settings.alert_reminder_hours)

    for key, f in findings.items():
        alert = live.get(key)
        if alert is None:
            alert = OpsAlert(
                dedup_key=key,
                rule=f.rule,
                severity=f.severity,
                title=f.title,
                detail=f.detail,
                status="open",
                opened_at=now,
                last_seen_at=now,
                notify_count=0,
            )
            db.add(alert)
            db.flush()
            if notify(f.title, f.detail, f.severity, {"alert_id": alert.alert_id, "rule": f.rule}):
                alert.last_notified_at = now
                alert.notify_count = 1
            stats["opened"] += 1
            continue
        alert.last_seen_at = now
        alert.detail = f.detail
        last = ops_monitor.aware(alert.last_notified_at)
        due_retry = alert.notify_count == 0  # earlier delivery found no working channel
        due_reminder = alert.status == "open" and last is not None and now - last >= reminder
        if due_retry or due_reminder:
            if notify(
                ("Reminder: " if due_reminder else "") + str(alert.title),
                f.detail,
                str(alert.severity),
                {"alert_id": alert.alert_id, "rule": f.rule},
            ):
                alert.last_notified_at = now
                alert.notify_count += 1
            stats["reminded"] += 1
        else:
            stats["unchanged"] += 1

    for key, alert in live.items():
        if key in findings:
            continue
        alert.status = "resolved"
        alert.resolved_at = now
        notify(
            f"Recovered: {alert.title}",
            "The condition is no longer present.",
            "info",
            {"alert_id": alert.alert_id, "rule": alert.rule},
        )
        stats["resolved"] += 1

    db.commit()
    return stats


def acknowledge(db: Session, alert_id: str, user: str) -> OpsAlert:
    alert = db.get(OpsAlert, alert_id)
    if alert is None:
        raise LookupError(alert_id)
    if alert.status == "open":
        alert.status = "acknowledged"
        alert.acknowledged_at = _now()
        alert.acknowledged_by = user
        db.commit()
    return alert


def list_alerts(db: Session, include_resolved_hours: int = 72) -> List[Dict[str, Any]]:
    cutoff = _now() - timedelta(hours=include_resolved_hours)
    rows = (
        db.query(OpsAlert)
        .filter((OpsAlert.status.in_(LIVE)) | (OpsAlert.resolved_at >= cutoff))
        .order_by(OpsAlert.opened_at.desc())
        .limit(200)
    )
    return [
        {
            "alert_id": a.alert_id,
            "rule": a.rule,
            "severity": a.severity,
            "title": a.title,
            "detail": a.detail,
            "status": a.status,
            "opened_at": ops_monitor.iso(a.opened_at),
            "last_seen_at": ops_monitor.iso(a.last_seen_at),
            "acknowledged_by": a.acknowledged_by,
            "resolved_at": ops_monitor.iso(a.resolved_at),
            "notified": a.notify_count > 0,
        }
        for a in rows
    ]

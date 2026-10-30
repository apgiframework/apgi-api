"""
Operational monitoring queries for the /console dashboard.

Everything here is derived from tables that exist today (participant_sessions,
trial_events, acquisition_uploads, tasks, webhook_deliveries). The planned
catalog/ops schemas (S2-CAT-01) are not deployed, so anything that cannot be
measured from those tables is returned as ``None`` and rendered as "unknown"
by the console; no value is ever estimated.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database.models import (
    AcquisitionUpload,
    OpsJob,
    ParticipantSession,
    ParticipantSessionStatus,
    Task,
    TaskStatus,
    TrialEvent,
    WebhookDelivery,
)
from app.services import job_tracker

# A running task with no completion after this long is treated as stuck.
STUCK_TASK_AFTER = timedelta(minutes=30)
# A committed upload still "pending" after this long never reached verification.
STUCK_UPLOAD_AFTER = timedelta(minutes=15)
# An in-progress participant session untouched for this long is stalled.
STALLED_SESSION_AFTER = timedelta(hours=24)
# A source is stale past twice its expected interval, unknown if it never reported.
SOURCE_INTERVALS: Dict[str, timedelta] = {
    "participant_sessions": timedelta(hours=24),
    "trial_events": timedelta(hours=24),
    "acquisition_uploads": timedelta(hours=24),
    "tasks": timedelta(hours=24),
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(ts: Any) -> Optional[datetime]:
    """SQLite returns naive datetimes; treat them as UTC."""
    if ts is None:
        return None
    out: datetime = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    return out


def iso(ts: Any) -> Optional[str]:
    ts = aware(ts)
    return ts.isoformat() if ts else None


def age_seconds(ts: Any, now: Optional[datetime] = None) -> Optional[int]:
    ts = aware(ts)
    if ts is None:
        return None
    return max(0, int(((now or utcnow()) - ts).total_seconds()))


def _safe(db: Session, fn: Callable[[], Any]) -> Any:
    """Run a panel query; a failing source yields None (unknown), never a guess."""
    try:
        return fn()
    except Exception:
        # A failed statement aborts the transaction on PostgreSQL; clear it so
        # the remaining panels can still be measured.
        db.rollback()
        return None


ORIGINS = ("synthetic", "human_observed", "unclassified")


def with_origin(query: Any, origin: Optional[str]) -> Any:
    """Restrict a ParticipantSession query to one origin class (None = all)."""
    if origin == "unclassified":
        return query.filter(ParticipantSession.data_origin.is_(None))
    if origin in ("synthetic", "human_observed"):
        return query.filter(ParticipantSession.data_origin == origin)
    return query


def _enum_val(v: Any) -> str:
    return str(getattr(v, "value", v))


# ---------------------------------------------------------------------------
# Freshness
# ---------------------------------------------------------------------------


def freshness(db: Session) -> List[Dict[str, Any]]:
    now = utcnow()
    latest: Dict[str, Callable[[], Any]] = {
        "participant_sessions": lambda: db.query(func.max(ParticipantSession.created_at)).scalar(),
        "trial_events": lambda: db.query(func.max(TrialEvent.received_at)).scalar(),
        "acquisition_uploads": lambda: db.query(func.max(AcquisitionUpload.updated_at)).scalar(),
        "tasks": lambda: db.query(func.max(Task.created_at)).scalar(),
    }
    rows = []
    for source, query in latest.items():
        ts = aware(_safe(db, query))
        interval = SOURCE_INTERVALS[source]
        if ts is None:
            state = "unknown"
        elif now - ts > 2 * interval:
            state = "stale"
        else:
            state = "fresh"
        rows.append(
            {
                "source": source,
                "last_event_at": iso(ts),
                "age_seconds": age_seconds(ts, now),
                "expected_interval_seconds": int(interval.total_seconds()),
                "state": state,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Panels
# ---------------------------------------------------------------------------


def panel_ingestion(db: Session, origin: Optional[str] = None) -> Dict[str, Any]:
    since = utcnow() - timedelta(hours=24)

    def sessions() -> Dict[str, int]:
        counts = {s.value: 0 for s in ParticipantSessionStatus}
        for status, n in with_origin(
            db.query(ParticipantSession.status, func.count()), origin
        ).group_by(ParticipantSession.status):
            counts[_enum_val(status)] = n
        return counts

    def uploads() -> Dict[str, int]:
        counts = {"pending": 0, "accepted": 0, "quarantined": 0, "rejected": 0}
        for status, n in db.query(AcquisitionUpload.status, func.count()).group_by(
            AcquisitionUpload.status
        ):
            counts[status] = n
        return counts

    return {
        "sessions_by_status": _safe(db, sessions),
        "trial_events_24h": _safe(
            db,
            lambda: db.query(func.count(TrialEvent.trial_event_id))
            .filter(TrialEvent.received_at >= since)
            .scalar(),
        ),
        "sessions_started_24h": _safe(
            db,
            lambda: with_origin(
                db.query(func.count(ParticipantSession.participant_session_id)), origin
            )
            .filter(ParticipantSession.created_at >= since)
            .scalar(),
        ),
        "uploads_by_status": _safe(db, uploads),
    }


def panel_jobs(db: Session) -> Dict[str, Any]:
    now = utcnow()

    def by_status() -> Dict[str, int]:
        counts = {s.value: 0 for s in TaskStatus}
        for status, n in db.query(Task.status, func.count()).group_by(Task.status):
            counts[_enum_val(status)] = n
        return counts

    def oldest_pending() -> Optional[int]:
        ts = db.query(func.min(Task.created_at)).filter(Task.status == TaskStatus.PENDING).scalar()
        return age_seconds(ts, now)

    def stuck() -> int:
        cutoff = now - STUCK_TASK_AFTER
        return int(
            db.query(func.count(Task.task_id))
            .filter(Task.status == TaskStatus.RUNNING, Task.started_at < cutoff)
            .scalar()
            or 0
        )

    def durable() -> Dict[str, int]:
        counts = {k: 0 for k in ("queued", "running", "succeeded", "failed", "dead")}
        counts.update({"stale_queued": 0, "lease_expired": 0})
        for job in db.query(OpsJob):
            counts[job_tracker.classify(job, now)] += 1
        return counts

    return {
        "durable_jobs": _safe(db, durable),
        "queue_backend": "celery",
        "tasks_by_status": _safe(db, by_status),
        "oldest_pending_age_seconds": _safe(db, oldest_pending),
        "stuck_running": _safe(db, stuck),
        "webhook_failed": _safe(
            db,
            lambda: db.query(func.count(WebhookDelivery.delivery_id))
            .filter(WebhookDelivery.status == "failed")
            .scalar(),
        ),
    }


def panel_quality(db: Session, origin: Optional[str] = None) -> Dict[str, Any]:
    """Scientific-quality indicators computable today. Kept apart from operations."""

    def completion() -> Dict[str, Any]:
        count = func.count(ParticipantSession.participant_session_id)
        done = (
            with_origin(db.query(count), origin)
            .filter(ParticipantSession.status == ParticipantSessionStatus.COMPLETED)
            .scalar()
            or 0
        )
        abandoned = (
            with_origin(db.query(count), origin)
            .filter(ParticipantSession.status == ParticipantSessionStatus.ABANDONED)
            .scalar()
            or 0
        )
        total = with_origin(db.query(count), origin).scalar() or 0
        return {
            "total": total,
            "completion_rate": round(done / total, 4) if total else None,
            "interruption_rate": round(abandoned / total, 4) if total else None,
        }

    def tiers() -> Dict[str, int]:
        out: Dict[str, int] = {}
        for tier, n in with_origin(
            db.query(ParticipantSession.device_tier, func.count()), origin
        ).group_by(ParticipantSession.device_tier):
            out[tier or "unknown"] = n
        return out

    def seq_gaps() -> Dict[str, int]:
        gap_query = db.query(
            TrialEvent.participant_session_id,
            func.min(TrialEvent.seq_no),
            func.max(TrialEvent.seq_no),
            func.count(TrialEvent.seq_no),
        ).filter(TrialEvent.seq_no.isnot(None))
        if origin:
            ids = with_origin(db.query(ParticipantSession.participant_session_id), origin)
            gap_query = gap_query.filter(TrialEvent.participant_session_id.in_(ids.subquery()))
        rows = gap_query.group_by(TrialEvent.participant_session_id).all()
        missing = [(hi - lo + 1) - n for _, lo, hi, n in rows]
        return {
            "sessions_checked": len(rows),
            "sessions_with_gaps": sum(1 for m in missing if m > 0),
            "missing_events": sum(m for m in missing if m > 0),
        }

    return {
        "completion": _safe(db, completion),
        "device_tier_distribution": _safe(db, tiers),
        "missing_events": _safe(db, seq_gaps),
        "indicators_available": 4,
        "indicators_planned": 14,
    }


def panel_origin(db: Session) -> Dict[str, Any]:
    """
    Synthetic vs human separation. Origin is a recorded fact on each session;
    NULL means unclassified and is never inferred as human or synthetic.
    """

    def counts() -> Dict[str, int]:
        out = {k: 0 for k in ORIGINS}
        for origin, n in db.query(ParticipantSession.data_origin, func.count()).group_by(
            ParticipantSession.data_origin
        ):
            out[origin or "unclassified"] = n
        return out

    c = _safe(db, counts)
    return {
        "synthetic": None if c is None else c["synthetic"],
        "human_observed": None if c is None else c["human_observed"],
        "unclassified": None if c is None else c["unclassified"],
        "note": "Unclassified sessions are not assumed human or synthetic.",
    }


def panel_unmeasured() -> Dict[str, Any]:
    """Panels whose sources are not connected (Cloud Monitoring, deletion ledger, backups)."""
    return {
        "retention_and_deletion": {
            "overdue_purges": None,
            "expiring_30d": None,
            "source": "deletion ledger (S3-GOV-04) not deployed",
        },
        "infrastructure": {
            "availability": None,
            "p95_latency_ms": None,
            "error_ratio_5xx": None,
            "last_backup_at": None,
            "last_restore_drill_at": None,
            "source": "Cloud Monitoring / Cloud SQL Admin API not connected",
        },
    }


def home(db: Session, origin: Optional[str] = None) -> Dict[str, Any]:
    fresh = freshness(db)
    return {
        "origin_filter": origin,
        "ingestion": panel_ingestion(db, origin),
        "jobs": panel_jobs(db),
        "quality": panel_quality(db, origin),
        "origin": panel_origin(db),
        **panel_unmeasured(),
        "freshness": fresh,
        "as_of": {f["source"]: f["last_event_at"] for f in fresh},
    }


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Failure:
    job_id: str
    kind: str
    ref: str
    group: str
    owner_role: str
    error: Any
    since: Any
    retry_eligible: bool
    correlation_id: Any = None

    def as_dict(self, now: datetime) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "ref": self.ref,
            "group": self.group,
            "owner_role": self.owner_role,
            "error": self.error,
            "since": iso(self.since),
            "age_seconds": age_seconds(self.since, now),
            "retry_eligible": self.retry_eligible,
            "correlation_id": self.correlation_id,
        }


JOB_GROUPS = (
    ("dead", "Dead-lettered jobs (retries exhausted)", "engineering"),
    ("failed", "Failed jobs", "engineering"),
    ("lease_expired", "Worker lost (lease expired)", "engineering"),
    ("stale_queued", "Queued but never started", "engineering"),
)


def collect_failures(db: Session, limit: int = 200) -> List[Dict[str, Any]]:
    now = utcnow()
    out: List[Failure] = []

    for t in (
        db.query(Task)
        .filter(Task.status == TaskStatus.FAILED)
        .order_by(Task.completed_at.desc())
        .limit(limit)
    ):
        out.append(
            Failure(
                f"task:{t.task_id}",
                "task",
                str(t.task_id),
                "Failed jobs",
                "engineering",
                t.error_message,
                t.completed_at or t.created_at,
                False,
            )
        )

    for t in db.query(Task).filter(
        Task.status == TaskStatus.RUNNING, Task.started_at < now - STUCK_TASK_AFTER
    ):
        out.append(
            Failure(
                f"task:{t.task_id}",
                "task",
                str(t.task_id),
                "Stuck jobs",
                "engineering",
                "Running past the lease window without completing",
                t.started_at,
                False,
            )
        )

    for u in (
        db.query(AcquisitionUpload)
        .filter(AcquisitionUpload.status.in_(["quarantined", "rejected"]))
        .order_by(AcquisitionUpload.updated_at.desc())
        .limit(limit)
    ):
        out.append(
            Failure(
                f"upload:{u.upload_id}",
                "upload",
                str(u.upload_id),
                "Quarantined or rejected chunks",
                "data-steward",
                f"{u.status}: {u.reason}" if u.reason else str(u.status),
                u.updated_at,
                False,
            )
        )

    tracked = {(j.kind, j.ref) for j in db.query(OpsJob.kind, OpsJob.ref)}

    for state_name, label, owner in JOB_GROUPS:
        for j in db.query(OpsJob).filter(
            OpsJob.status.in_(("queued", "running", "failed", "dead"))
        ):
            if job_tracker.classify(j, now) != state_name:
                continue
            out.append(
                Failure(
                    f"job:{j.job_id}",
                    "job",
                    f"{j.kind}:{j.ref}",
                    label,
                    owner,
                    j.error or label,
                    j.finished_at or j.started_at or j.enqueued_at,
                    True,
                    j.correlation_id,
                )
            )

    for u in db.query(AcquisitionUpload).filter(
        AcquisitionUpload.status == "pending",
        AcquisitionUpload.committed.is_(True),
        AcquisitionUpload.updated_at < now - STUCK_UPLOAD_AFTER,
    ):
        if (job_tracker.UPLOAD_VERIFY, u.upload_id) in tracked:
            continue
        out.append(
            Failure(
                f"upload:{u.upload_id}",
                "upload",
                str(u.upload_id),
                "Verification never ran",
                "engineering",
                "Committed but not verified",
                u.updated_at,
                True,
            )
        )

    for s in db.query(ParticipantSession).filter(
        ParticipantSession.status == ParticipantSessionStatus.COMPLETED,
        ParticipantSession.scores.is_(None),
        ParticipantSession.completed_at < now - STUCK_UPLOAD_AFTER,
    ):
        if (job_tracker.SCORING, s.participant_session_id) in tracked:
            continue
        out.append(
            Failure(
                f"scoring:{s.participant_session_id}",
                "session",
                str(s.participant_session_id),
                "Completed but not scored",
                "engineering",
                "Session completed without scores",
                s.completed_at,
                True,
                s.correlation_id,
            )
        )

    for d in (
        db.query(WebhookDelivery)
        .filter(WebhookDelivery.status == "failed")
        .order_by(WebhookDelivery.last_attempt_at.desc())
        .limit(limit)
    ):
        out.append(
            Failure(
                f"webhook:{d.delivery_id}",
                "webhook",
                str(d.delivery_id),
                "Webhook delivery failures",
                "engineering",
                d.error_message,
                d.last_attempt_at or d.created_at,
                False,
            )
        )

    out.sort(key=lambda f: aware(f.since) or now)
    return [f.as_dict(now) for f in out[:limit]]


# ---------------------------------------------------------------------------
# Retry
# ---------------------------------------------------------------------------


class RetryNotEligible(Exception):
    pass


def _dispatch(db: Session, kind: str, ref: str) -> None:
    """Record the durable job, then hand it to the queue (idempotent by kind/ref)."""
    if kind == job_tracker.UPLOAD_VERIFY:
        from app.tasks.acquisition_tasks import verify_acquisition_upload

        job_tracker.record_enqueue(db, kind, ref)
        verify_acquisition_upload.delay(ref)
    elif kind == job_tracker.SCORING:
        from app.tasks.scoring_tasks import score_participant_session

        job_tracker.record_enqueue(db, kind, ref)
        score_participant_session.delay(ref)
    else:
        raise RetryNotEligible("this job kind has no safe retry")


def retry_job(db: Session, job_id: str) -> Dict[str, Any]:
    """Re-enqueue an eligible job. All targets are idempotent by ID."""
    kind, _, ref = job_id.partition(":")
    now = utcnow()

    if kind == "job":
        job = db.get(OpsJob, ref)
        if job is None:
            raise LookupError(job_id)
        if job_tracker.classify(job, now) not in (
            "failed",
            "dead",
            "lease_expired",
            "stale_queued",
        ):
            raise RetryNotEligible("job is not failed, lost or stale")
        _dispatch(db, str(job.kind), str(job.ref))
    elif kind == "upload":
        rec = db.get(AcquisitionUpload, ref)
        if rec is None:
            raise LookupError(job_id)
        if not (rec.committed and rec.status == "pending"):
            raise RetryNotEligible(
                "only committed uploads still pending verification can be retried"
            )
        _dispatch(db, job_tracker.UPLOAD_VERIFY, ref)
    elif kind == "scoring":
        ps = db.get(ParticipantSession, ref)
        if ps is None:
            raise LookupError(job_id)
        if ps.status != ParticipantSessionStatus.COMPLETED or ps.scores is not None:
            raise RetryNotEligible("only completed, unscored sessions can be re-scored")
        _dispatch(db, job_tracker.SCORING, ref)
    else:
        raise RetryNotEligible("this job kind has no safe retry")

    return {"job_id": job_id, "requeued_at": now.isoformat()}


# ---------------------------------------------------------------------------
# Catalog (derived view over existing tables)
# ---------------------------------------------------------------------------

CATALOG_KINDS = ("session", "upload", "task")


def catalog_search(
    db: Session,
    kind: Optional[str],
    q: Optional[str],
    status: Optional[str],
    page: int,
    per_page: int,
    origin: Optional[str] = None,
) -> Dict[str, Any]:
    if origin:
        kind = "session"  # origin is recorded on sessions only
    items: List[Dict[str, Any]] = []
    total = 0

    def like(col: Any) -> Any:
        return col.ilike(f"%{q}%")

    if kind in (None, "session"):
        qs = with_origin(db.query(ParticipantSession), origin)
        if q:
            qs = qs.filter(like(ParticipantSession.participant_session_id))
        if status:
            qs = qs.filter(ParticipantSession.status == status)
        total += qs.count()
        for s in qs.order_by(ParticipantSession.created_at.desc()).limit(per_page * page):
            items.append(
                {
                    "kind": "session",
                    "id": s.participant_session_id,
                    "status": _enum_val(s.status),
                    "device_tier": s.device_tier,
                    "study_id": s.study_id,
                    "created_at": iso(s.created_at),
                    "origin": s.data_origin,
                }
            )
    if kind in (None, "upload"):
        qu = db.query(AcquisitionUpload)
        if q:
            qu = qu.filter(like(AcquisitionUpload.upload_id) | like(AcquisitionUpload.session_id))
        if status:
            qu = qu.filter(AcquisitionUpload.status == status)
        total += qu.count()
        for u in qu.order_by(AcquisitionUpload.created_at.desc()).limit(per_page * page):
            items.append(
                {
                    "kind": "upload",
                    "id": u.upload_id,
                    "status": u.status,
                    "device_tier": None,
                    "study_id": None,
                    "created_at": iso(u.created_at),
                    "origin": None,
                }
            )
    if kind in (None, "task"):
        qt = db.query(Task)
        if q:
            qt = qt.filter(like(Task.task_id) | like(Task.task_type))
        if status:
            qt = qt.filter(Task.status == status)
        total += qt.count()
        for t in qt.order_by(Task.created_at.desc()).limit(per_page * page):
            items.append(
                {
                    "kind": "task",
                    "id": t.task_id,
                    "status": _enum_val(t.status),
                    "device_tier": None,
                    "study_id": None,
                    "created_at": iso(t.created_at),
                    "origin": None,
                }
            )

    items.sort(key=lambda i: i["created_at"] or "", reverse=True)
    start = (page - 1) * per_page
    return {"items": items[start : start + per_page], "total": total}


def session_detail(db: Session, session_id: str) -> Optional[Dict[str, Any]]:
    s = db.get(ParticipantSession, session_id)
    if s is None:
        return None
    events: List[Dict[str, Any]] = [{"at": iso(s.created_at), "event": "session_created"}]
    if s.started_at:
        events.append({"at": iso(s.started_at), "event": "started"})

    batches = (
        db.query(
            func.count(TrialEvent.trial_event_id),
            func.min(TrialEvent.received_at),
            func.max(TrialEvent.received_at),
        )
        .filter(TrialEvent.participant_session_id == session_id)
        .one()
    )
    if batches[0]:
        events.append(
            {"at": iso(batches[1]), "event": f"first trial events received ({batches[0]} total)"}
        )
        events.append({"at": iso(batches[2]), "event": "latest trial events received"})
    for u in db.query(AcquisitionUpload).filter(AcquisitionUpload.session_id == session_id):
        events.append({"at": iso(u.created_at), "event": f"chunk {u.sequence} uploaded"})
        if u.status != "pending":
            events.append(
                {
                    "at": iso(u.updated_at),
                    "event": f"chunk {u.sequence} {u.status}",
                    "detail": u.reason or None,
                }
            )
    if s.completed_at:
        events.append({"at": iso(s.completed_at), "event": "completed"})
    if s.scores is not None:
        events.append({"at": None, "event": "scored (scoring time not recorded)"})
    events.sort(key=lambda e: e["at"] or "")
    return {
        "kind": "session",
        "id": s.participant_session_id,
        "status": _enum_val(s.status),
        "study_id": s.study_id,
        "device_tier": s.device_tier,
        "norm_eligible": s.norm_eligible,
        "trial_event_count": batches[0],
        "scored": s.scores is not None,
        "events": events,
        "origin": s.data_origin,
        "correlation_id": s.correlation_id,
    }


def classify_session(db: Session, session_id: str, origin: str) -> Dict[str, Any]:
    """Record a session's factual origin. Reclassifying is allowed but returns the prior value."""
    if origin not in ("synthetic", "human_observed"):
        raise ValueError("origin must be synthetic or human_observed")
    ps = db.get(ParticipantSession, session_id)
    if ps is None:
        raise LookupError(session_id)
    previous = ps.data_origin
    ps.data_origin = origin  # type: ignore[assignment]
    db.commit()
    return {"id": session_id, "origin": origin, "previous": previous}

"""
Durable job tracking (S2-CAT-05, first slice).

A job row is written *before* dispatch, so a crash between "decided to run"
and "worker started" leaves a visible ``queued`` row. Workers mark start
(with a bounded lease) and finish. An independent sweep, not the worker,
decides that a lease expired or the retry budget is spent. Tracking is
best-effort: it must never break the work it observes.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterator, Optional

from sqlalchemy.orm import Session

from app.database.models import OpsJob
from app.middleware.correlation import correlation_id_context, get_correlation_id

logger = logging.getLogger(__name__)

UPLOAD_VERIFY = "upload_verify"
SCORING = "scoring"
KINDS = (UPLOAD_VERIFY, SCORING)

LEASE = timedelta(minutes=30)
QUEUED_TOO_LONG = timedelta(minutes=15)
OPEN_STATES = ("queued", "running")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(ts: Any) -> Optional[datetime]:
    if ts is None:
        return None
    out: datetime = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    return out


def record_enqueue(db: Session, kind: str, ref: str) -> OpsJob:
    """Create (or re-arm) the job for ``kind``/``ref``; idempotent while one is open."""
    job = (
        db.query(OpsJob)
        .filter(OpsJob.kind == kind, OpsJob.ref == ref)
        .order_by(OpsJob.enqueued_at.desc())
        .first()
    )
    now = _now()
    if job is not None and job.status in OPEN_STATES:
        job.status = "queued"  # type: ignore[assignment]
        job.enqueued_at = now  # type: ignore[assignment]
    elif job is not None and job.status in ("failed", "dead"):
        job.status = "queued"  # type: ignore[assignment]
        job.enqueued_at = now  # type: ignore[assignment]
        job.error = None  # type: ignore[assignment]
        job.finished_at = None  # type: ignore[assignment]
        if job.attempts >= job.max_attempts:
            job.attempts = 0  # type: ignore[assignment]  # operator-initiated retry restores budget
    else:
        job = OpsJob(
            kind=kind,
            ref=ref,
            status="queued",
            attempts=0,
            enqueued_at=now,
            correlation_id=get_correlation_id(),
        )
        db.add(job)
    db.commit()
    return job


def classify(job: OpsJob, now: Optional[datetime] = None) -> str:
    """Effective state, derived at read time: adds 'stale_queued' and 'lease_expired'."""
    now = now or _now()
    if job.status == "queued" and now - _aware(job.enqueued_at) > QUEUED_TOO_LONG:  # type: ignore[operator]
        return "stale_queued"
    lease = _aware(job.lease_expires_at)
    if job.status == "running" and lease is not None and now > lease:
        return "lease_expired"
    return str(job.status)


def sweep(db: Session) -> Dict[str, int]:
    """Watchdog: persist expired leases as failed, or dead once attempts are exhausted."""
    now = _now()
    counts = {"failed": 0, "dead": 0}
    for job in db.query(OpsJob).filter(OpsJob.status == "running"):
        if classify(job, now) != "lease_expired":
            continue
        dead = job.attempts >= job.max_attempts
        job.status = "dead" if dead else "failed"  # type: ignore[assignment]
        job.error = "worker lease expired without completion"  # type: ignore[assignment]
        job.finished_at = now  # type: ignore[assignment]
        counts["dead" if dead else "failed"] += 1
    db.commit()
    return counts


@contextmanager
def track(kind: str, ref: str) -> Iterator[None]:
    """Worker-side wrapper: mark started, then succeeded/failed. Never raises on its own."""
    from app.database.connection import SessionLocal

    cid_token = None
    try:
        db = SessionLocal()
        try:
            job = (
                db.query(OpsJob)
                .filter(OpsJob.kind == kind, OpsJob.ref == ref)
                .order_by(OpsJob.enqueued_at.desc())
                .first()
            )
            if job is None:  # dispatched by a path that predates tracking
                job = OpsJob(kind=kind, ref=ref, status="queued", attempts=0, enqueued_at=_now())
                db.add(job)
            job.status = "running"  # type: ignore[assignment]
            job.attempts += 1  # type: ignore[assignment]
            job.started_at = _now()  # type: ignore[assignment]
            job.lease_expires_at = _now() + LEASE  # type: ignore[assignment]
            if job.correlation_id:
                cid_token = correlation_id_context.set(job.correlation_id)  # type: ignore[arg-type]
            db.commit()
            job_id = job.job_id
        finally:
            db.close()
    except Exception:
        logger.warning("job tracking unavailable at start", exc_info=True)
        job_id = None

    error: Optional[str] = None
    try:
        yield
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:2000]
        raise
    finally:
        if job_id is not None:
            _finish(job_id, error)
        if cid_token is not None:
            correlation_id_context.reset(cid_token)


def _finish(job_id: Any, error: Optional[str]) -> None:
    from app.database.connection import SessionLocal

    try:
        db = SessionLocal()
        try:
            job = db.get(OpsJob, job_id)
            if job is not None:
                job.status = "succeeded" if error is None else "failed"  # type: ignore[assignment]
                if error is not None and job.attempts >= job.max_attempts:
                    job.status = "dead"  # type: ignore[assignment]
                job.error = error  # type: ignore[assignment]
                job.finished_at = _now()  # type: ignore[assignment]
                job.lease_expires_at = None  # type: ignore[assignment]
                db.commit()
        finally:
            db.close()
    except Exception:
        logger.warning("job tracking unavailable at finish", exc_info=True)

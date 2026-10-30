"""
Operations read API for the /console dashboard.

Admin-only, aggregate-first. Every response uses the shared envelope
``{"data": ..., "as_of": {source: iso8601 | null}}``.
"""

import logging
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.models.schemas import ErrorResponse
from app.services import ops_monitor, platform_health
from app.services.authorization import (
    Permission,
    TokenPayload,
    get_current_user,
    log_audit_event,
    require_permission,
)

logger = logging.getLogger(__name__)

# Console scopes. Study-level scientific data additionally needs OPS_QUALITY; anything that
# changes state needs OPS_WRITE. Only the admin role holds them today: researchers must not
# get OPS_QUALITY until study ownership exists, or one researcher could read another study.
READ = require_permission(Permission.OPS_READ)
WRITE = require_permission(Permission.OPS_WRITE)
QUALITY = require_permission(Permission.OPS_QUALITY)
GUARDS = (READ, WRITE, QUALITY)

ACCESS_PURPOSES = ("operations_support", "research_qa", "security_investigation")

router = APIRouter(
    prefix="/v1/ops",
    tags=["Operations"],
    dependencies=[Depends(READ)],
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Forbidden"},
    },
)


def _audit(
    db: Session,
    user: TokenPayload,
    action: str,
    resource_type: str,
    resource_id: Optional[str],
    details: Dict[str, Any],
) -> None:
    """
    Record a sensitive read through the shared (signed, hash-chained) audit writer.
    ``strict`` means a failed write refuses the read (500) instead of serving it unaudited.
    """
    log_audit_event(
        db=db,
        user_id=user.user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details,
        strict=True,
    )


def _envelope(data: Any, as_of: Optional[Dict[str, Optional[str]]] = None) -> Dict[str, Any]:
    return {"data": data, "as_of": as_of or {"api": ops_monitor.utcnow().isoformat()}}


@router.get("/home", summary="Console home: operational and scientific panels")
async def ops_home(
    origin: Optional[str] = Query(None, pattern="^(synthetic|human_observed|unclassified)$"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    data = ops_monitor.home(db, origin)
    as_of = data.pop("as_of")
    infra = await run_in_threadpool(platform_health.snapshot)
    data["infrastructure"] = infra
    as_of.update(infra.get("as_of", {}))
    return _envelope(data, as_of)


@router.get("/freshness", summary="Per-source freshness")
async def ops_freshness(db: Session = Depends(get_db)) -> Dict[str, Any]:
    rows = ops_monitor.freshness(db)
    return _envelope(rows, {r["source"]: r["last_event_at"] for r in rows})


@router.get("/failures", summary="Grouped failures with owner role and retry eligibility")
async def ops_failures(
    limit: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> Dict[str, Any]:
    _audit(db, current_user, "ops:failures_read", "failures", None, {"limit": limit})
    return _envelope(ops_monitor.collect_failures(db, limit))


@router.post(
    "/jobs/{job_id}:retry",
    dependencies=[Depends(WRITE)],
    summary="Retry an eligible job",
    responses={404: {"description": "Unknown job"}, 409: {"description": "Not eligible"}},
)
async def ops_retry(job_id: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    try:
        result = ops_monitor.retry_job(db, job_id)
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    except ops_monitor.RetryNotEligible as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    logger.info("ops retry", extra={"job_id": job_id})
    return _envelope(result)


@router.get("/catalog", summary="Search sessions, uploads and tasks")
async def ops_catalog(
    kind: Optional[str] = Query(None, pattern="^(session|upload|task)$"),
    q: Optional[str] = Query(None, max_length=100),
    status_filter: Optional[str] = Query(None, alias="status", max_length=32),
    origin: Optional[str] = Query(None, pattern="^(synthetic|human_observed|unclassified)$"),
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> Dict[str, Any]:
    _audit(
        db,
        current_user,
        "ops:catalog_search",
        "catalog",
        None,
        {"kind": kind, "q_given": bool(q), "status": status_filter, "origin": origin, "page": page},
    )
    return _envelope(ops_monitor.catalog_search(db, kind, q, status_filter, page, per_page, origin))


@router.get("/catalog/session/{session_id}", summary="Session detail with pipeline timeline")
async def ops_session_detail(
    session_id: str,
    purpose: str = Query(..., pattern="^(operations_support|research_qa|security_investigation)$"),
    db: Session = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> Dict[str, Any]:
    # Restricted read: a declared purpose is required and recorded before data is returned.
    _audit(
        db,
        current_user,
        "ops:session_read",
        "participant_session",
        session_id,
        {"purpose": purpose, "scope": "ops:read"},
    )
    detail = ops_monitor.session_detail(db, session_id)
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    return _envelope(detail)


class ClassifyRequest(BaseModel):
    origin: Literal["synthetic", "human_observed"]


@router.post(
    "/sessions/{session_id}:classify",
    dependencies=[Depends(WRITE)],
    summary="Record a session's factual data origin",
)
async def ops_classify(
    session_id: str,
    body: ClassifyRequest,
    db: Session = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> Dict[str, Any]:
    try:
        result = ops_monitor.classify_session(db, session_id, body.origin)
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    log_audit_event(
        db=db,
        user_id=current_user.user_id,
        action="participant_session:classify_origin",
        resource_type="participant_session",
        resource_id=session_id,
        details={"origin": body.origin, "previous": result["previous"]},
    )
    return _envelope(result)


@router.get("/alerts", summary="Open and recently resolved alerts")
async def ops_alerts(db: Session = Depends(get_db)) -> Dict[str, Any]:
    from app.services import ops_alerts

    return _envelope(ops_alerts.list_alerts(db))


@router.post(
    "/alerts/{alert_id}:ack",
    dependencies=[Depends(WRITE)],
    summary="Acknowledge an alert",
)
async def ops_ack_alert(
    alert_id: str,
    db: Session = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> Dict[str, Any]:
    from app.services import ops_alerts

    try:
        alert = ops_alerts.acknowledge(db, alert_id, str(current_user.user_id))
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert not found")
    log_audit_event(
        db=db,
        user_id=current_user.user_id,
        action="ops_alert:acknowledge",
        resource_type="ops_alert",
        resource_id=alert_id,
        details={"rule": alert.rule},
    )
    return _envelope({"alert_id": alert_id, "status": alert.status})


@router.get(
    "/platform", summary="Platform health readings (Cloud Monitoring, backups, local probes)"
)
async def ops_platform(refresh: bool = Query(False)) -> Dict[str, Any]:
    snap = await run_in_threadpool(platform_health.snapshot, refresh)
    return _envelope(snap, snap["as_of"])


@router.get(
    "/data-quality",
    summary="The fourteen scientific data-quality indicators",
    dependencies=[Depends(QUALITY)],
)
async def ops_data_quality(
    study_id: Optional[str] = Query(None, max_length=36),
    battery_id: Optional[str] = Query(None, max_length=36),
    origin: Optional[str] = Query(None, pattern="^(synthetic|human_observed|unclassified)$"),
    db: Session = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> Dict[str, Any]:
    from app.services import data_quality

    _audit(
        db,
        current_user,
        "ops:data_quality_read",
        "data_quality",
        None,
        {
            "study_id": study_id,
            "battery_id": battery_id,
            "origin": origin,
            "purpose": "research_qa",
        },
    )
    return _envelope(data_quality.compute(db, study_id, battery_id, origin))


@router.get(
    "/studies",
    summary="Studies and batteries for the data-quality filters",
    dependencies=[Depends(QUALITY)],
)
async def ops_studies(db: Session = Depends(get_db)) -> Dict[str, Any]:
    from app.database.models import Battery, Study

    studies = [
        {"study_id": s.study_id, "name": s.name} for s in db.query(Study).order_by(Study.name)
    ]
    batteries = [
        {"battery_id": b.battery_id, "study_id": b.study_id, "name": f"{b.name} v{b.version}"}
        for b in db.query(Battery).order_by(Battery.name)
    ]
    return _envelope({"studies": studies, "batteries": batteries})


class PartitionRequest(BaseModel):
    partition: Literal["development", "confirmation", "holdout"]


@router.post(
    "/enrollments/{enrollment_id}:partition",
    dependencies=[Depends(WRITE)],
    summary="Assign an enrollment to an analysis partition (set once)",
    responses={
        404: {"description": "Unknown enrollment"},
        409: {"description": "Already assigned"},
    },
)
async def ops_assign_partition(
    enrollment_id: str,
    body: PartitionRequest,
    db: Session = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> Dict[str, Any]:
    from app.database.models import StudyEnrollment

    enrollment = db.get(StudyEnrollment, enrollment_id)
    if enrollment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Enrollment not found")
    if enrollment.partition is not None:
        # Re-assigning after data exist would let holdout participants leak into
        # development analyses, so an assignment is final.
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Partition is already assigned and cannot change"
        )
    enrollment.partition = body.partition
    db.commit()
    log_audit_event(
        db=db,
        user_id=current_user.user_id,
        action="study_enrollment:assign_partition",
        resource_type="study_enrollment",
        resource_id=enrollment_id,
        details={"partition": body.partition},
    )
    return _envelope({"enrollment_id": enrollment_id, "partition": body.partition})

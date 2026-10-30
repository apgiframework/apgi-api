"""
Operations read API for the /console dashboard.

Admin-only, aggregate-first. Every response uses the shared envelope
``{"data": ..., "as_of": {source: iso8601 | null}}``.
"""

import logging
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.models.schemas import ErrorResponse
from app.services import ops_monitor
from app.services.authorization import (
    Permission,
    TokenPayload,
    get_current_user,
    log_audit_event,
    require_permission,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1/ops",
    tags=["Operations"],
    dependencies=[Depends(require_permission(Permission.SYSTEM_ADMIN))],
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Forbidden"},
    },
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
    return _envelope(data, as_of)


@router.get("/freshness", summary="Per-source freshness")
async def ops_freshness(db: Session = Depends(get_db)) -> Dict[str, Any]:
    rows = ops_monitor.freshness(db)
    return _envelope(rows, {r["source"]: r["last_event_at"] for r in rows})


@router.get("/failures", summary="Grouped failures with owner role and retry eligibility")
async def ops_failures(
    limit: int = Query(200, ge=1, le=500), db: Session = Depends(get_db)
) -> Dict[str, Any]:
    return _envelope(ops_monitor.collect_failures(db, limit))


@router.post(
    "/jobs/{job_id}:retry",
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
) -> Dict[str, Any]:
    return _envelope(ops_monitor.catalog_search(db, kind, q, status_filter, page, per_page, origin))


@router.get("/catalog/session/{session_id}", summary="Session detail with pipeline timeline")
async def ops_session_detail(session_id: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    detail = ops_monitor.session_detail(db, session_id)
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    return _envelope(detail)


class ClassifyRequest(BaseModel):
    origin: Literal["synthetic", "human_observed"]


@router.post("/sessions/{session_id}:classify", summary="Record a session's factual data origin")
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


@router.post("/alerts/{alert_id}:ack", summary="Acknowledge an alert")
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

"""Acquisition chunk upload routes (apgi-system offline-first uploader).

Contract (client: ``apgi_framework.interoperability.api_client``):

* ``POST /v1/acquisition/uploads``              manifest -> signed bucket URL (stage A)
* ``POST /v1/acquisition/uploads/{id}/commit``  stage C; queues the SHA-256 job
* ``GET  /v1/acquisition/uploads/status?key=``  ``<session_id>/<object_name>`` lookup

Stage B is the client's direct ``PUT`` to the bucket; the API token is never sent there.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.config import settings
from app.database.connection import get_db
from app.models.schemas import ErrorResponse, TokenPayload
from app.schemas.acquisition import UploadInitRequest, UploadInitResponse, UploadStatusResponse
from app.services import acquisition_ingest as ingest
from app.services.acquisition_storage import AcquisitionObjectStore, get_acquisition_store
from app.services.authorization import Role, log_audit_event, require_any_role

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1/acquisition",
    tags=["Acquisition"],
    responses={
        403: {"model": ErrorResponse, "description": "Forbidden"},
        404: {"model": ErrorResponse, "description": "Not Found"},
    },
)

_ALLOWED = [Role.ADMIN, Role.RESEARCHER]


def get_verification_enqueuer() -> Callable[[str], None]:
    """Dependency returning the function that queues the SHA-256 verification job."""

    def enqueue(upload_id: str) -> None:
        from app.database.connection import SessionLocal
        from app.services import job_tracker
        from app.tasks.acquisition_tasks import verify_acquisition_upload

        # Durable record first: a crash before dispatch leaves a visible queued job.
        with SessionLocal() as db:
            job_tracker.record_enqueue(db, job_tracker.UPLOAD_VERIFY, upload_id)
        verify_acquisition_upload.delay(upload_id)

    return enqueue


def _store(store: Optional[AcquisitionObjectStore]) -> AcquisitionObjectStore:
    if store is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "acquisition storage is not configured"
        )
    return store


def _is_admin(user: TokenPayload) -> bool:
    return Role.ADMIN.value in user.roles


def _view(rec: object) -> UploadStatusResponse:
    return UploadStatusResponse(
        upload_id=getattr(rec, "upload_id"),
        status=getattr(rec, "status"),
        reason=getattr(rec, "reason") or "",
        sha256=getattr(rec, "sha256"),
    )


@router.post("/uploads", response_model=UploadInitResponse, status_code=status.HTTP_201_CREATED)
def init_upload(
    body: UploadInitRequest,
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(require_any_role(_ALLOWED)),
    store: Optional[AcquisitionObjectStore] = Depends(get_acquisition_store),
) -> UploadInitResponse:
    """Stage A: register a manifest and return a time-limited signed PUT URL."""
    try:
        result = ingest.init_upload(
            db,
            _store(store),
            principal=user.user_id,
            is_admin=_is_admin(user),
            session_id=body.session_id,
            object_name=body.object_name,
            size_bytes=body.size_bytes,
            crc32c_value=body.crc32c,
            sha256=body.sha256,
            sequence=body.sequence,
            url_ttl_s=settings.acquisition_url_ttl_s,
        )
    except ingest.UploadForbidden as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except ingest.Conflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    log_audit_event(
        db,
        user_id=user.user_id,
        action="acquisition.upload.init",
        resource_type="acquisition_session",
        resource_id=body.session_id,
        details={"object_name": body.object_name, "size_bytes": body.size_bytes},
    )
    return UploadInitResponse(**result)  # type: ignore[arg-type]


@router.get("/uploads/status", response_model=UploadStatusResponse)
def upload_status_by_key(
    key: str = Query(..., min_length=3, description="<session_id>/<object_name>"),
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(require_any_role(_ALLOWED)),
) -> UploadStatusResponse:
    """Latest known state of an object; 404 when it was never initialised."""
    try:
        rec = ingest.status_for_key(db, key, user.user_id, is_admin=_is_admin(user))
    except ingest.UploadForbidden as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    if rec is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown object")
    return _view(rec)


@router.post("/uploads/{upload_id}/commit", response_model=UploadStatusResponse)
def commit_upload(
    upload_id: str,
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(require_any_role(_ALLOWED)),
    store: Optional[AcquisitionObjectStore] = Depends(get_acquisition_store),
    enqueue: Callable[[str], None] = Depends(get_verification_enqueuer),
) -> UploadStatusResponse:
    """Stage C: verify object presence, size, sequence and CRC32C, then queue SHA-256."""
    try:
        rec = ingest.commit(
            db,
            _store(store),
            upload_id=upload_id,
            principal=user.user_id,
            is_admin=_is_admin(user),
        )
    except ingest.UnknownUpload as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown upload") from exc
    except ingest.UploadForbidden as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    if rec.status == ingest.PENDING:
        try:
            enqueue(str(rec.upload_id))
        except Exception:  # broker outage must not fail the commit; the status stays pending
            logger.exception("could not enqueue verification for %s", rec.upload_id)
    log_audit_event(
        db,
        user_id=user.user_id,
        action="acquisition.upload.commit",
        resource_type="acquisition_session",
        resource_id=str(rec.session_id),
        details={"upload_id": upload_id, "status": rec.status},
    )
    return _view(rec)

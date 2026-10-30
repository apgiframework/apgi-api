"""
Participant and Consent Routes (Phase 2, governing doc §7).

Every route here checks resource ownership before touching participant
data: either the caller IS the linked user (participant.user_id ==
current_user.user_id) or the caller holds RESEARCHER/ADMIN. A participant
with no linked user account (the fully anonymous recruitment flow) is only
reachable by staff through this authenticated API — a separate
capability-token flow for anonymous participants is out of scope here and
tracked as a follow-up, not something this endpoint set silently pretends
to support.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.database.models import Consent, ConsentType, DataDeletion, Participant
from app.models.schemas import ErrorResponse, TokenPayload
from app.schemas.research import (
    ConsentGrantRequest,
    ConsentResponse,
    ConsentRevokeRequest,
    ConsentStatusResponse,
    ParticipantCreateRequest,
    ParticipantResponse,
)
from app.services.authorization import (
    Role,
    get_current_user,
    has_any_role,
    log_audit_event,
)
from app.services.consent import has_active_consent, record_consent, revoke_consent
from app.services.explorer_contract import consent_text_for
from app.services.rls import set_rls_context

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1/participants",
    tags=["Participants"],
    responses={
        403: {"model": ErrorResponse, "description": "Forbidden"},
        404: {"model": ErrorResponse, "description": "Not Found"},
    },
)


def _get_participant_or_404(db: Session, participant_id: str) -> Participant:
    participant = (
        db.query(Participant)
        .filter(Participant.participant_id == participant_id, Participant.is_deleted.is_(False))
        .first()
    )
    if participant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Participant not found")
    return participant


def _check_participant_access(participant: Participant, current_user: TokenPayload) -> None:
    is_staff = has_any_role(current_user.roles, [Role.ADMIN, Role.RESEARCHER])
    if is_staff:
        return
    if participant.user_id and participant.user_id == current_user.user_id:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized for this participant"
    )


@router.post(
    "",
    response_model=ParticipantResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Enroll a participant",
)
async def create_participant(
    request: ParticipantCreateRequest,
    http_request: Request,
    db: Session = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> Participant:
    participant = Participant(
        user_id=current_user.user_id,
        external_ref=request.external_ref,
        encrypted_contact_email=request.contact_email,
        encrypted_demographics=(
            None if request.demographics is None else json.dumps(request.demographics)
        ),
    )
    db.add(participant)
    db.commit()
    db.refresh(participant)

    log_audit_event(
        db=db,
        user_id=current_user.user_id,
        action="participant:create",
        resource_type="participant",
        resource_id=participant.participant_id,  # type: ignore[arg-type]
        ip_address=http_request.client.host if http_request.client else None,
    )
    return participant


@router.get(
    "/{participant_id}",
    response_model=ParticipantResponse,
    summary="Get a participant",
)
async def get_participant(
    participant_id: str,
    db: Session = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> Participant:
    set_rls_context(db, current_user)
    participant = _get_participant_or_404(db, participant_id)
    _check_participant_access(participant, current_user)
    return participant


@router.delete(
    "/{participant_id}",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Erase a participant (right to erasure)",
    description="Immediately tombstones the participant and clears PII; enqueues the cascading "
    "cross-system deletion job (Cloud SQL detail rows, raw trial events in Cloud Storage, "
    "de-identified BigQuery export) — see app/tasks/deletion_tasks.py.",
)
async def delete_participant(
    participant_id: str,
    http_request: Request,
    db: Session = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> dict[str, str]:
    set_rls_context(db, current_user)
    participant = _get_participant_or_404(db, participant_id)
    _check_participant_access(participant, current_user)

    db.add(DataDeletion(participant_id=participant_id, requested_by=current_user.user_id))
    for consent_type in ConsentType:
        revoke_consent(db, participant_id, consent_type)
    participant.is_deleted = True  # type: ignore[assignment]
    participant.deleted_at = datetime.now(timezone.utc)  # type: ignore[assignment]
    participant.encrypted_contact_email = None
    participant.encrypted_demographics = None
    db.commit()

    log_audit_event(
        db=db,
        user_id=current_user.user_id,
        action="participant:erase",
        resource_type="participant",
        resource_id=participant_id,
        ip_address=http_request.client.host if http_request.client else None,
    )

    try:
        from app.tasks.deletion_tasks import erase_participant_data

        erase_participant_data.delay(participant_id)
    except Exception:
        logger.error(
            "Failed to enqueue cascading deletion task",
            exc_info=True,
            extra={"participant_id": participant_id},
        )

    return {"status": "accepted", "participant_id": participant_id}


@router.post(
    "/{participant_id}/consents",
    response_model=ConsentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Grant a versioned consent",
)
async def grant_consent(
    participant_id: str,
    request: ConsentGrantRequest,
    http_request: Request,
    db: Session = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> Consent:
    set_rls_context(db, current_user)
    participant = _get_participant_or_404(db, participant_id)
    _check_participant_access(participant, current_user)

    # The text is always the server-served canonical text for the configured
    # version; clients cannot supply their own (ConsentGrantRequest forbids it).
    canonical = consent_text_for(request.consent_type.value, request.locale)
    if canonical is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No canonical consent text for this purpose and locale",
        )
    consent_text = canonical["body"]
    consent = record_consent(
        db,
        participant_id=participant_id,
        consent_type=request.consent_type,
        consent_text=consent_text,
        ip_address=http_request.client.host if http_request.client else None,
    )
    log_audit_event(
        db=db,
        user_id=current_user.user_id,
        action="consent:grant",
        resource_type="participant",
        resource_id=participant_id,
        details={"consent_type": request.consent_type.value, "version": consent.version},
    )
    return consent


@router.post(
    "/{participant_id}/consents/revoke",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a consent",
)
async def revoke_consent_route(
    participant_id: str,
    request: ConsentRevokeRequest,
    db: Session = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> None:
    set_rls_context(db, current_user)
    participant = _get_participant_or_404(db, participant_id)
    _check_participant_access(participant, current_user)

    revoke_consent(db, participant_id, request.consent_type)
    log_audit_event(
        db=db,
        user_id=current_user.user_id,
        action="consent:revoke",
        resource_type="participant",
        resource_id=participant_id,
        details={"consent_type": request.consent_type.value},
    )


@router.get(
    "/{participant_id}/consents/status",
    response_model=ConsentStatusResponse,
    summary="Effective status of every consent purpose",
)
async def get_consent_status(
    participant_id: str,
    db: Session = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> ConsentStatusResponse:
    set_rls_context(db, current_user)
    participant = _get_participant_or_404(db, participant_id)
    _check_participant_access(participant, current_user)

    return ConsentStatusResponse(
        product_use=has_active_consent(db, participant_id, ConsentType.PRODUCT_USE),
        recontact=has_active_consent(db, participant_id, ConsentType.RECONTACT),
        research_participation=has_active_consent(
            db, participant_id, ConsentType.RESEARCH_PARTICIPATION
        ),
        data_sharing=has_active_consent(db, participant_id, ConsentType.DATA_SHARING),
    )

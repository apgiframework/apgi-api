"""Generic version-addressed instruments and Layer-1 item responses.

GET  /v1/instruments/{id}/versions/{version}        (public, scoring key stays server-side)
POST /v1/participant-sessions/{id}/responses        (idempotent item_responses batch)
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.database.models import ConsentType, ItemResponse, ParticipantSessionStatus
from app.models.schemas import TokenPayload
from app.routes.trials import _check_session_access, _get_session_or_404
from app.schemas.explorer import ItemBankResponse
from app.services import explorer_contract as contract
from app.services.authorization import get_current_user
from app.services.consent import require_any_consent
from app.services.instrument_registry import get_version
from app.services.rls import set_rls_context

router = APIRouter(tags=["Instruments"])


class ItemAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(..., min_length=1, max_length=64)
    item_id: int
    value: int


class ItemResponseBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument_id: str
    instrument_version: str
    instrument_sha256: str = Field(..., min_length=64, max_length=64)
    responses: list[ItemAnswer] = Field(..., min_length=1, max_length=500)


class ItemResponseAck(BaseModel):
    event_id: str
    item_id: int
    payload_sha256: str
    status: Literal["accepted", "duplicate"]


class ItemResponseAckBatch(BaseModel):
    acks: list[ItemResponseAck]


@router.get("/v1/instruments/{instrument_id}/versions/{version}", response_model=ItemBankResponse)
async def get_instrument_version(instrument_id: str, version: str) -> dict[str, Any]:
    entry = get_version(instrument_id, version)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown instrument version")
    return entry.bank


@router.post(
    "/v1/participant-sessions/{participant_session_id}/responses",
    response_model=ItemResponseAckBatch,
    status_code=status.HTTP_201_CREATED,
    summary="Idempotent Layer-1 item responses",
    description="Rejects the whole batch when the instrument hash differs from the registered "
    "version. Same event_id + same content is acknowledged as a duplicate; same event_id with "
    "different content, or a second answer to an item, is a 409.",
)
async def post_item_responses(
    participant_session_id: str,
    request: ItemResponseBatch,
    db: Session = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> ItemResponseAckBatch:
    set_rls_context(db, current_user)
    session = _get_session_or_404(db, participant_session_id)
    _check_session_access(db, session, current_user)
    require_any_consent(
        db,
        str(session.participant_id),
        ConsentType.PRODUCT_USE,
        ConsentType.RESEARCH_PARTICIPATION,
    )
    if session.status in (ParticipantSessionStatus.ABANDONED, ParticipantSessionStatus.COMPLETED):
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Session is {session.status.value}")

    entry = get_version(request.instrument_id, request.instrument_version)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown instrument version")
    if request.instrument_sha256 != entry.sha256:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="instrument_sha256 differs from the registered instrument version",
        )

    prepared: list[tuple[ItemAnswer, str]] = []
    for index, answer in enumerate(request.responses):
        problem = entry.validate_answer(answer.item_id, answer.value)
        if problem:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"responses[{index}]: {problem}"
            )
        digest = contract.sha256_hex(
            contract.canonical_json({"item_id": answer.item_id, "value": answer.value})
        )
        prepared.append((answer, digest))

    acks: list[ItemResponseAck] = []
    for answer, digest in prepared:
        existing = (
            db.query(ItemResponse)
            .filter(
                ItemResponse.participant_session_id == participant_session_id,
                (ItemResponse.event_id == answer.event_id)
                | (
                    (ItemResponse.instrument_id == entry.instrument_id)
                    & (ItemResponse.item_id == str(answer.item_id))
                ),
            )
            .first()
        )
        if existing is not None:
            if existing.event_id == answer.event_id and existing.payload_sha256 == digest:
                acks.append(
                    ItemResponseAck(
                        event_id=answer.event_id,
                        item_id=answer.item_id,
                        payload_sha256=digest,
                        status="duplicate",
                    )
                )
                continue
            db.rollback()
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"event_id or item {answer.item_id} already recorded with different content",
            )
        db.add(
            ItemResponse(
                participant_session_id=participant_session_id,
                event_id=answer.event_id,
                instrument_id=entry.instrument_id,
                instrument_version=entry.version,
                instrument_sha256=entry.sha256,
                item_id=str(answer.item_id),
                response_value=answer.value,
                payload_sha256=digest,
            )
        )
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status.HTTP_409_CONFLICT, detail="Concurrent upload; retry")
        acks.append(
            ItemResponseAck(
                event_id=answer.event_id,
                item_id=answer.item_id,
                payload_sha256=digest,
                status="accepted",
            )
        )
    db.commit()
    return ItemResponseAckBatch(acks=acks)

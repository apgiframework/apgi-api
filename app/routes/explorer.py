"""Explorer research-client contract.

Public, cacheable reference data (config, item bank, consent texts, score
allow-list) plus the two authenticated session operations the client needs:
idempotent journal upload with a `server_verified` acknowledgement, and the
allow-listed score read-back. The client never scores research sessions and
never renders bundled items.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database.connection import get_db
from app.database.models import (
    ConsentType,
    ParticipantSession,
    ParticipantSessionStatus,
    TrialEvent,
)
from app.models.schemas import TokenPayload
from app.routes.trials import _check_session_access, _get_session_or_404
from app.schemas.explorer import (
    ConsentPurposeInfo,
    ConsentTextResponse,
    EventAck,
    EventAckBatch,
    ExplorerConfigResponse,
    ExplorerResultResponse,
    InstrumentRef,
    ItemBankResponse,
    JournalEventBatch,
    ScoreAllowlistEntry,
    ScoreAllowlistResponse,
)
from app.services import explorer_contract as contract
from app.services.authorization import get_current_user, log_audit_event
from app.services.consent import require_consent
from app.services.rls import set_rls_context
from app.services.trial_storage import write_raw_trial_event

router = APIRouter(tags=["Explorer"])

EXPLORER_TASK_TYPE = "explorer_item"
_RESEARCH_CONSENTS = (ConsentType.PRODUCT_USE, ConsentType.RESEARCH_PARTICIPATION)


@router.get("/v1/explorer/config", response_model=ExplorerConfigResponse)
async def get_explorer_config() -> ExplorerConfigResponse:
    bank = contract.item_bank()
    purposes = []
    for consent_type, version in settings.consent_current_versions.items():
        text = contract.consent_text_for(consent_type)
        purposes.append(
            ConsentPurposeInfo(
                consent_type=consent_type,
                version=version,
                status=text["status"] if text else "missing",
                required_for_research=bool(text and text["required_for_research"]),
            )
        )
    return ExplorerConfigResponse(
        research_enabled=contract.research_enabled(),
        instrument=InstrumentRef(
            instrument_id=bank["instrument_id"],
            bank_version=bank["bank_version"],
            sha256=bank["sha256"],
            item_count=len(bank["items"]),
        ),
        consent_purposes=purposes,
    )


@router.get("/v1/consent-texts", response_model=list[ConsentTextResponse])
async def list_consent_texts(locale: str = Query("en", min_length=2, max_length=10)) -> list[dict]:  # type: ignore[type-arg]
    texts = [contract.consent_text_for(ct, locale) for ct in settings.consent_current_versions]
    return [t for t in texts if t is not None]


@router.get("/v1/instruments/apgi-explorer-35", response_model=ItemBankResponse)
async def get_item_bank() -> dict:  # type: ignore[type-arg]
    return contract.item_bank()


@router.get(
    "/v1/instruments/apgi-explorer-35/score-allowlist", response_model=ScoreAllowlistResponse
)
async def get_score_allowlist() -> ScoreAllowlistResponse:
    return ScoreAllowlistResponse(
        instrument_id=contract.INSTRUMENT_ID,
        fields=[
            ScoreAllowlistEntry(field=e["field"], label=e["label"])
            for e in contract.SCORE_ALLOWLIST.values()
        ],
    )


def _ack(event_id: str, seq_no: int, payload_sha256: str, state: str) -> EventAck:
    return EventAck(
        event_id=event_id,
        seq_no=seq_no,
        payload_sha256=payload_sha256,
        status=state,  # type: ignore[arg-type]
        server_verified=True,
    )


@router.post(
    "/v1/participant-sessions/{participant_session_id}/events",
    response_model=EventAckBatch,
    summary="Idempotent journal upload",
    description="Each event is acknowledged exactly once with server_verified=true after the "
    "server has re-hashed the canonical payload and persisted it. Re-sending an event with the "
    "same event_id, seq_no and hash is acknowledged as a duplicate; reusing an event_id or "
    "seq_no with different content is a 409.",
)
async def upload_journal_events(
    participant_session_id: str,
    request: JournalEventBatch,
    db: Session = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> EventAckBatch:
    set_rls_context(db, current_user)
    session = _get_session_or_404(db, participant_session_id)
    _check_session_access(db, session, current_user)
    require_consent(db, str(session.participant_id), *_RESEARCH_CONSENTS)

    closed = session.status == ParticipantSessionStatus.COMPLETED
    if session.status == ParticipantSessionStatus.ABANDONED:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Session is abandoned")

    # Validate the whole batch before writing anything.
    for index, event in enumerate(request.events):
        problem = contract.validate_payload(event.payload)
        if problem is None and contract.sha256_hex(contract.canonical_json(event.payload)) != (
            event.payload_sha256
        ):
            problem = "payload_sha256 does not match the canonical payload"
        if problem:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"events[{index}]: {problem}"
            )

    acks: list[EventAck] = []
    for event in request.events:
        existing = (
            db.query(TrialEvent)
            .filter(
                TrialEvent.participant_session_id == participant_session_id,
                (TrialEvent.event_id == event.event_id) | (TrialEvent.seq_no == event.seq_no),
            )
            .first()
        )
        if existing is not None:
            if (
                existing.event_id == event.event_id
                and existing.seq_no == event.seq_no
                and existing.payload_sha256 == event.payload_sha256
            ):
                acks.append(
                    _ack(event.event_id, event.seq_no, str(existing.payload_sha256), "duplicate")
                )
                continue
            db.rollback()
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"event_id/seq_no {event.seq_no} already recorded with different content",
            )
        if closed:
            db.rollback()
            raise HTTPException(
                status.HTTP_409_CONFLICT, detail="Session is complete; no new events accepted"
            )

        raw_object = write_raw_trial_event(
            participant_session_id, EXPLORER_TASK_TYPE, event.seq_no, event.payload
        )
        latency = event.payload.get("latency_us")
        db.add(
            TrialEvent(
                participant_session_id=participant_session_id,
                task_type=EXPLORER_TASK_TYPE,
                trial_index=event.seq_no,
                response_value={
                    "item_id": event.payload["item_id"],
                    "value": event.payload["value"],
                    "previous_value": event.payload.get("previous_value"),
                },
                rt_ms=latency / 1000 if latency is not None else None,
                raw_gcs_object=raw_object,
                event_id=event.event_id,
                seq_no=event.seq_no,
                payload_sha256=event.payload_sha256,
            )
        )
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise HTTPException(
                status.HTTP_409_CONFLICT, detail="Concurrent upload of the same event; retry"
            )
        acks.append(_ack(event.event_id, event.seq_no, event.payload_sha256, "accepted"))

    if session.status == ParticipantSessionStatus.SCHEDULED:
        from datetime import datetime, timezone

        session.status = ParticipantSessionStatus.IN_PROGRESS
        session.started_at = datetime.now(timezone.utc)  # type: ignore[assignment]
    db.commit()
    return EventAckBatch(acks=acks)


@router.get(
    "/v1/participant-sessions/{participant_session_id}/explorer-result",
    response_model=ExplorerResultResponse,
    summary="Allow-listed scores for an uploaded Explorer session",
)
async def get_explorer_result(
    participant_session_id: str,
    db: Session = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> ExplorerResultResponse:
    set_rls_context(db, current_user)
    session: ParticipantSession = _get_session_or_404(db, participant_session_id)
    _check_session_access(db, session, current_user)
    require_consent(db, str(session.participant_id), ConsentType.PRODUCT_USE)

    rows = (
        db.query(TrialEvent)
        .filter(
            TrialEvent.participant_session_id == participant_session_id,
            TrialEvent.task_type == EXPLORER_TASK_TYPE,
        )
        .order_by(TrialEvent.seq_no)
        .all()
    )
    answers = {int(r.response_value["item_id"]): int(r.response_value["value"]) for r in rows}  # type: ignore[index]
    if set(answers) != contract.ITEM_IDS:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Session incomplete: {len(answers)} of {len(contract.ITEM_IDS)} items uploaded",
        )
    log_audit_event(
        db=db,
        user_id=current_user.user_id,
        action="explorer_result:read",
        resource_type="participant_session",
        resource_id=participant_session_id,
        details={},
    )
    return ExplorerResultResponse(
        participant_session_id=participant_session_id,
        instrument_id=contract.INSTRUMENT_ID,
        instrument_sha256=contract.item_bank()["sha256"],
        analysis_mode=contract.ANALYSIS_MODE,
        excluded_from_norms=True,
        scores=contract.allowlisted(contract.score_answers(answers)),
    )

"""
Participant Session and Trial Event Routes (Phase 2).

Trial ingestion is the hot path apgi-battery's jsPsych tasks hit once per
trial (or once per batch at the end of a block): every write is gated on
RESEARCH_PARTICIPATION consent, ownership-checked against the parent
participant session, rate-limited by the global RateLimitingMiddleware
(app/middleware/rate_limiting.py — no per-route opt-in needed), and
schema-validated with `extra="forbid"` so a client can't smuggle fields
like `correct` for a task type whose grading happens server-side only via
raw_payload.

The session-completion contract (POST .../complete) is what apgi-battery
calls exactly once, at the end of a battery administration: it is
idempotent (repeat calls return the same completed state rather than
erroring) and is the hook Phase 3-4's scoring pipeline attaches to.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.database.models import (
    Battery,
    ConsentType,
    Participant,
    ParticipantSession,
    ParticipantSessionStatus,
    Study,
    TrialEvent,
)
from app.models.schemas import ErrorResponse, TokenPayload
from app.schemas.research import (
    ParticipantSessionCreateRequest,
    ParticipantSessionResponse,
    SessionCompletionResponse,
    SessionQualityRequest,
    TrialEventBatchIngestRequest,
    TrialEventResponse,
)
from app.services.authorization import Role, get_current_user, has_any_role, log_audit_event
from app.services.consent import require_consent
from app.services.rls import set_rls_context
from app.services.screening import (
    TIER_CONFIG,
    assign_tier,
    email_hash_for,
    expected_form_label,
    get_or_assign_form_order,
    hash_identifier,
    screen_session,
    timing_error_distribution,
)
from app.services.trial_storage import write_raw_trial_event

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Trials"],
    responses={
        403: {"model": ErrorResponse, "description": "Forbidden"},
        404: {"model": ErrorResponse, "description": "Not Found"},
    },
)


def _get_session_or_404(db: Session, participant_session_id: str) -> ParticipantSession:
    session = (
        db.query(ParticipantSession)
        .filter(ParticipantSession.participant_session_id == participant_session_id)
        .first()
    )
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Participant session not found"
        )
    return session


def _check_session_access(
    db: Session, session: ParticipantSession, current_user: TokenPayload
) -> Participant:
    if has_any_role(current_user.roles, [Role.ADMIN, Role.RESEARCHER]):
        participant = (
            db.query(Participant)
            .filter(Participant.participant_id == session.participant_id)
            .first()
        )
        if participant is None:  # pragma: no cover - FK guarantees this can't happen
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Participant not found"
            )
        return participant

    participant = (
        db.query(Participant).filter(Participant.participant_id == session.participant_id).first()
    )
    if participant is None or participant.user_id != current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized for this session"
        )
    return participant


def event_payload_sha256(event: object) -> str:
    """SHA-256 over the canonical event content (idempotency / conflict detection)."""
    body = event.model_dump(mode="json")  # type: ignore[attr-defined]
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _data_quality_alert(participant_session_id: str, event_id: str, kind: str) -> None:
    logger.warning(
        "data-quality alert: %s",
        kind,
        extra={"participant_session_id": participant_session_id, "event_id": event_id},
    )


def find_sequence_gaps(db: Session, participant_session_id: str) -> List[int]:
    """Missing seq_nos between 0 and the highest seen (non-fatal; reported)."""
    seqs = [
        r[0]
        for r in db.query(TrialEvent.seq_no).filter(
            TrialEvent.participant_session_id == participant_session_id,
            TrialEvent.seq_no.isnot(None),
        )
    ]
    if not seqs:
        return []
    return sorted(set(range(0, max(seqs) + 1)) - set(seqs))


@router.post(
    "/v1/participant-sessions",
    response_model=ParticipantSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_participant_session(
    request: ParticipantSessionCreateRequest,
    http_request: Request,
    db: Session = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> ParticipantSession:
    set_rls_context(db, current_user)

    participant = (
        db.query(Participant).filter(Participant.participant_id == request.participant_id).first()
    )
    if participant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Participant not found")
    if (
        not has_any_role(current_user.roles, [Role.ADMIN, Role.RESEARCHER])
        and participant.user_id != current_user.user_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized for this participant"
        )

    if db.query(Study).filter(Study.study_id == request.study_id).first() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Study not found")
    if db.query(Battery).filter(Battery.battery_id == request.battery_id).first() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Battery not found")

    require_consent(db, request.participant_id, ConsentType.RESEARCH_PARTICIPATION)

    next_index = (
        db.query(func.max(ParticipantSession.session_index))
        .filter(
            ParticipantSession.participant_id == request.participant_id,
            ParticipantSession.study_id == request.study_id,
        )
        .scalar()
        or 0
    ) + 1

    session = ParticipantSession(
        participant_id=request.participant_id,
        study_id=request.study_id,
        battery_id=request.battery_id,
        session_index=next_index,
        status=ParticipantSessionStatus.IN_PROGRESS,
        started_at=datetime.now(timezone.utc),
        device_fingerprint_hash=(
            hash_identifier(request.device_fingerprint) if request.device_fingerprint else None
        ),
        email_hash=email_hash_for(participant),
        data_origin=request.data_origin,
        administration_mode=request.administration_mode,
        app_version=request.app_version,
        browser_family=request.browser_family,
        correlation_id=getattr(http_request.state, "correlation_id", None),
    )
    db.add(session)
    form_order = get_or_assign_form_order(db, request.participant_id, request.study_id)
    db.commit()
    db.refresh(session)
    session.expected_form_label = expected_form_label(form_order, next_index)

    log_audit_event(
        db=db,
        user_id=current_user.user_id,
        action="participant_session:create",
        resource_type="participant_session",
        resource_id=session.participant_session_id,  # type: ignore[arg-type]
        details={"session_index": next_index},
    )
    return session


@router.post(
    "/v1/participant-sessions/{participant_session_id}/trials",
    response_model=List[TrialEventResponse],
    status_code=status.HTTP_201_CREATED,
)
async def ingest_trial_events(
    participant_session_id: str,
    request: TrialEventBatchIngestRequest,
    db: Session = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> List[TrialEvent]:
    set_rls_context(db, current_user)
    session = _get_session_or_404(db, participant_session_id)
    _check_session_access(db, session, current_user)

    if session.status not in (
        ParticipantSessionStatus.IN_PROGRESS,
        ParticipantSessionStatus.SCHEDULED,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot ingest trials into a session with status {session.status.value}",
        )

    require_consent(db, str(session.participant_id), ConsentType.RESEARCH_PARTICIPATION)

    existing_by_id = {
        t.event_id: t
        for t in db.query(TrialEvent).filter(
            TrialEvent.participant_session_id == participant_session_id,
            TrialEvent.event_id.in_([e.event_id for e in request.events]),
        )
    }
    seq_owner = {
        t.seq_no: t.event_id
        for t in db.query(TrialEvent).filter(
            TrialEvent.participant_session_id == participant_session_id,
            TrialEvent.seq_no.in_([e.seq_no for e in request.events]),
        )
    }

    # Validate the whole batch before storing anything: a conflict stores nothing.
    plan: List[tuple] = []
    batch_ids: dict = {}
    for event in request.events:
        digest = event_payload_sha256(event)
        prior = existing_by_id.get(event.event_id)
        if prior is None and event.event_id in batch_ids:
            prior = batch_ids[event.event_id]
        if prior is not None:
            prior_hash = prior.payload_sha256 if isinstance(prior, TrialEvent) else prior
            if prior_hash != digest:
                _data_quality_alert(participant_session_id, event.event_id, "payload_conflict")
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"event_id {event.event_id} already stored with a different payload",
                )
            plan.append((event, digest, prior if isinstance(prior, TrialEvent) else None))
            continue
        owner = seq_owner.get(event.seq_no)
        if owner is not None and owner != event.event_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"seq_no {event.seq_no} already used by a different event",
            )
        batch_ids[event.event_id] = digest
        plan.append((event, digest, None))

    created: List[TrialEvent] = []
    seen_new: set = set()
    for event, digest, prior in plan:
        if prior is not None:
            prior.duplicate = True  # type: ignore[attr-defined]
            created.append(prior)
            continue
        if event.event_id in seen_new:
            continue  # same event twice in one batch: store once
        seen_new.add(event.event_id)
        raw_gcs_object = None
        if event.raw_payload is not None:
            raw_gcs_object = write_raw_trial_event(
                participant_session_id, event.task_type, event.trial_index, event.raw_payload
            )

        trial = TrialEvent(
            participant_session_id=participant_session_id,
            event_id=event.event_id,
            seq_no=event.seq_no,
            payload_sha256=digest,
            timing_provenance=event.timing_provenance,
            task_type=event.task_type,
            trial_index=event.trial_index,
            response_value=event.response_value,
            rt_ms=event.rt_ms,
            correct=event.correct,
            raw_gcs_object=raw_gcs_object,
        )
        db.add(trial)
        created.append(trial)

    if session.status == ParticipantSessionStatus.SCHEDULED:
        session.status = ParticipantSessionStatus.IN_PROGRESS
        session.started_at = datetime.now(timezone.utc)  # type: ignore[assignment]

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Conflicting trial: (task_type, trial_index) or seq_no already recorded",
        )
    for trial in created:
        duplicate = getattr(trial, "duplicate", False)
        db.refresh(trial)
        trial.duplicate = duplicate  # type: ignore[attr-defined]

    return created


@router.post(
    "/v1/participant-sessions/{participant_session_id}/quality",
    response_model=ParticipantSessionResponse,
    summary="Submit device telemetry and validity signals",
)
async def submit_session_quality(
    participant_session_id: str,
    request: SessionQualityRequest,
    db: Session = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> ParticipantSession:
    set_rls_context(db, current_user)
    session = _get_session_or_404(db, participant_session_id)
    _check_session_access(db, session, current_user)

    if request.tier_config_version != TIER_CONFIG["version"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Unknown tier_config_version; server is on {TIER_CONFIG['version']}",
        )

    distribution = timing_error_distribution(request.timing_errors_ms)
    telemetry = {
        "timing_error": distribution,
        "dropped_frame_fraction": request.dropped_frame_fraction,
        "fullscreen": request.fullscreen,
        "is_mobile": request.is_mobile,
        "visibility_changes": request.visibility_changes,
    }
    tier, _ = assign_tier(telemetry, request.attention_checks_failed)
    session.device_tier = tier  # type: ignore[assignment]
    session.tier_config_version = request.tier_config_version  # type: ignore[assignment]
    session.timing_error_distribution = distribution  # type: ignore[assignment]
    db.commit()

    screen_session(
        db,
        session,
        questionnaire=request.questionnaire_responses,
        attention_checks={
            "passed": request.attention_checks_passed,
            "failed": request.attention_checks_failed,
        },
        bot_signals={
            "honeypot": request.honeypot,
            "completion_seconds": request.completion_seconds,
            "webdriver": request.webdriver,
        },
        telemetry=telemetry,
    )
    db.refresh(session)
    return session


@router.post(
    "/v1/participant-sessions/{participant_session_id}/complete",
    response_model=SessionCompletionResponse,
    summary="Session completion contract",
    description="Marks a participant session complete. Idempotent: calling this again on an "
    "already-completed session returns the same result rather than erroring.",
)
async def complete_participant_session(
    participant_session_id: str,
    db: Session = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> SessionCompletionResponse:
    set_rls_context(db, current_user)
    session = _get_session_or_404(db, participant_session_id)
    _check_session_access(db, session, current_user)

    trial_count = (
        db.query(func.count(TrialEvent.trial_event_id))
        .filter(TrialEvent.participant_session_id == participant_session_id)
        .scalar()
        or 0
    )

    missing = (
        []
        if session.status == ParticipantSessionStatus.COMPLETED
        else find_sequence_gaps(db, participant_session_id)
    )
    if missing:
        return SessionCompletionResponse(
            participant_session_id=participant_session_id,
            status=session.status,
            trial_count=trial_count,
            awaiting_inputs=True,
            missing_seq_nos=missing,
        )

    if session.status != ParticipantSessionStatus.COMPLETED:
        session.status = ParticipantSessionStatus.COMPLETED
        session.completed_at = datetime.now(timezone.utc)  # type: ignore[assignment]
        db.commit()
        if session.screening is None:
            # Never blocks completion: a session that skipped the quality
            # step is recorded as unscreened and kept out of norms.
            screen_session(db, session)
        db.refresh(session)

        log_audit_event(
            db=db,
            user_id=current_user.user_id,
            action="participant_session:complete",
            resource_type="participant_session",
            resource_id=participant_session_id,
            details={"trial_count": trial_count},
        )

        # Phase 3-4 attaches scoring here (app/services/scoring.py). Enqueued
        # best-effort — a scoring failure must never fail session completion
        # for the client, which has already finished the battery.
        try:
            from app.services import job_tracker
            from app.tasks.scoring_tasks import score_participant_session

            job_tracker.record_enqueue(db, job_tracker.SCORING, participant_session_id)
            score_participant_session.delay(participant_session_id)
        except Exception:
            logger.info(
                "Scoring task not enqueued (scoring pipeline not yet configured)",
                extra={"participant_session_id": participant_session_id},
            )

    return SessionCompletionResponse(
        participant_session_id=participant_session_id,
        status=session.status,
        trial_count=trial_count,
        completed_at=session.completed_at,  # type: ignore[arg-type]
    )

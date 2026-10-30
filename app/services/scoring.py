"""
Scoring service (Phase 3-4, governing doc §0.0/§5).

Aggregates a completed ParticipantSession's TrialEvent rows into per-task
summary scores. Two things distinguish this from a plain groupby-and-average:

  - Parallel-form handling: the administered battery's `form_label` is
    recorded alongside each task's scores, because norms (app/routes/norms.py)
    and psychometrics (app/routes/instrument.py) must never pool raw scores
    across forms without accounting for form — that's exactly the practice-
    effect confound parallel forms exist to avoid.
  - Session-index handling: `session_index` (this participant's 1st, 2nd,
    ... administration within the study) is recorded alongside scores so
    Phase 5's longitudinal metrics (ICC/SEM/MDC95 per index) can select
    same-index or adjacent-index pairs without re-deriving index from
    session ordering each time.
"""

from __future__ import annotations

import hashlib
import json
import logging
import statistics
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.database.models import (
    Battery,
    ModelVersion,
    ParticipantSession,
    TrialEvent,
)
from app.services.authorization import log_audit_event
from app.services.version_lattice import build_stamps, validate_stamps

logger = logging.getLogger(__name__)


def _task_summary(trials: list[TrialEvent]) -> dict[str, Any]:
    rts = [t.rt_ms for t in trials if t.rt_ms is not None]
    corrects = [1.0 if t.correct else 0.0 for t in trials if t.correct is not None]

    summary: dict[str, Any] = {"n_trials": len(trials)}
    if corrects:
        summary["accuracy"] = round(statistics.mean(corrects), 4)
    if rts:
        summary["mean_rt_ms"] = round(statistics.mean(rts), 2)  # type: ignore[type-var]
        summary["median_rt_ms"] = round(statistics.median(rts), 2)  # type: ignore[type-var]
        if len(rts) > 1:
            summary["sd_rt_ms"] = round(statistics.stdev(rts), 2)  # type: ignore[type-var]
    return summary


class ModelBindingError(RuntimeError):
    """The model bound at authorisation cannot be loaded; scoring must not proceed."""


def _sha256_json(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _input_sha256(db: Session, participant_session_id: str) -> str:
    """Hash of exactly the trial events the score was computed from."""
    trials = (
        db.query(TrialEvent)
        .filter(TrialEvent.participant_session_id == participant_session_id)
        .order_by(TrialEvent.task_type, TrialEvent.trial_index)
        .all()
    )
    return _sha256_json(
        [
            [t.task_type, t.trial_index, t.response_value, t.rt_ms, t.correct, t.payload_sha256]
            for t in trials
        ]
    )


def compute_session_scores(db: Session, participant_session: ParticipantSession) -> dict[str, Any]:
    """Compute per-task-type summary scores for a session's trial events.

    Returns a JSON-serializable dict suitable for ParticipantSession.scores:
        {
          "form_label": "A",
          "battery_version": "1.0",
          "session_index": 1,
          "tasks": {"stroop": {"n_trials": 40, "accuracy": 0.9, ...}, ...},
        }
    """
    trials = (
        db.query(TrialEvent)
        .filter(TrialEvent.participant_session_id == participant_session.participant_session_id)
        .all()
    )

    by_task: dict[str, list[TrialEvent]] = {}
    for trial in trials:
        by_task.setdefault(trial.task_type, []).append(trial)  # type: ignore[arg-type]

    battery = db.query(Battery).filter(Battery.battery_id == participant_session.battery_id).first()

    return {
        "form_label": battery.form_label if battery else None,
        "battery_version": battery.version if battery else None,
        "session_index": participant_session.session_index,
        "tasks": {task_type: _task_summary(t) for task_type, t in by_task.items()},
    }


def score_session(db: Session, participant_session_id: str) -> Optional[ParticipantSession]:
    """Compute and persist scores for a session; sets model_version_id to the
    currently active ModelVersion, if one is configured. Returns the updated
    session, or None if it doesn't exist."""
    participant_session = (
        db.query(ParticipantSession)
        .filter(ParticipantSession.participant_session_id == participant_session_id)
        .first()
    )
    if participant_session is None:
        logger.warning(
            "score_session: participant session not found",
            extra={"participant_session_id": participant_session_id},
        )
        return None

    scores = compute_session_scores(db, participant_session)
    # The model was bound when processing was authorised (session creation).
    # Load exactly that version or fail; never fall back to whatever is active now.
    bound_model: Optional[ModelVersion] = None
    if participant_session.model_version_id is not None:
        bound_model = (
            db.query(ModelVersion)
            .filter(ModelVersion.model_version_id == participant_session.model_version_id)
            .first()
        )
        if bound_model is None:
            raise ModelBindingError(
                f"Model version {participant_session.model_version_id} bound to session "
                f"{participant_session_id} no longer exists"
            )
    active_model = bound_model
    scores["stamps"] = validate_stamps(
        build_stamps(
            battery_version=scores.get("battery_version"),
            model_id=str(active_model.model_version_id) if active_model is not None else None,
            model_version=str(active_model.version) if active_model is not None else None,
            feature_version=(
                str(active_model.feature_version)
                if active_model is not None and active_model.feature_version
                else None
            ),
        )
    )

    participant_session.scores = scores  # type: ignore[assignment]
    # The audit write commits the score with it, so a score never exists
    # without its chained record: with signing configured, a failed audit
    # write rolls the score back (strict) instead of serving it unaudited.
    log_audit_event(
        db=db,
        action="score.generate",
        resource_type="participant_session",
        resource_id=participant_session_id,
        details={
            "input_sha256": _input_sha256(db, participant_session_id),
            "output_sha256": _sha256_json(scores),
            "stamps": scores["stamps"],
        },
        strict=bool(settings.audit_signing_key),
    )
    db.commit()
    db.refresh(participant_session)
    return participant_session

"""
Scoring Celery Tasks (Phase 3-4).

Triggered by app/routes/trials.py's session-completion contract. Scoring
and de-identified export are two separate, independently-logged steps: an
export failure must never lose the computed scores, and a scoring failure
must never silently produce a stale/partial export.
"""

from __future__ import annotations

import logging

from celery import shared_task

from app.database.connection import SessionLocal
from app.services.authorization import log_audit_event
from app.services.bigquery_export import export_session
from app.services.job_tracker import SCORING, track
from app.services.scoring import score_session

logger = logging.getLogger(__name__)


def _score_and_export(participant_session_id: str) -> None:
    db = SessionLocal()
    try:
        scored = score_session(db, participant_session_id)
        if scored is None:
            return

        log_audit_event(
            db=db,
            action="score:generate",
            resource_type="participant_session",
            resource_id=participant_session_id,
            details={"task_count": len(scored.scores.get("tasks", {})) if scored.scores else 0},
        )

        exported = export_session(db, participant_session_id)
        logger.info(
            "Scoring pipeline completed",
            extra={"participant_session_id": participant_session_id, "bigquery_exported": exported},
        )
    except Exception:
        logger.error(
            "Scoring pipeline failed",
            exc_info=True,
            extra={"participant_session_id": participant_session_id},
        )
        raise
    finally:
        db.close()


@shared_task(name="score_participant_session")  # type: ignore[untyped-decorator]
def score_participant_session(participant_session_id: str) -> None:
    with track(SCORING, participant_session_id):
        _score_and_export(participant_session_id)

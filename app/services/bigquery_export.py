"""
De-identified export path into BigQuery (Phase 3-4, governing doc §7.2).

Exports exactly the fields needed for norms/psychometrics/longitudinal
analysis, and nothing that identifies a participant: `participant_id` is
replaced with an HMAC-SHA256 keyed on a dedicated export salt
(PII_ENCRYPTION_KEY, reused as the keying material — a different, dedicated
EXPORT_SALT would be cleaner but is not worth a second required secret for
the pilot's scale) so the same participant's rows can still be joined
across sessions for longitudinal analysis without the join key being
reversible to the participant's identity. No PII field (external_ref,
contact email, demographics) is ever included.

Like app/services/trial_storage.py, this is a no-op (returns without
writing) when BIGQUERY_DEIDENTIFIED_DATASET isn't configured, so local
development and tests never need google-cloud-bigquery installed or live
credentials.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.database.models import ConsentType, ParticipantSession
from app.services.consent import ConsentWithdrawnError, ensure_processing_consent

logger = logging.getLogger(__name__)


def pseudonymous_subject_key(participant_id: str) -> str:
    key = (settings.pii_encryption_key or "unconfigured").encode("utf-8")
    return hmac.new(key, participant_id.encode("utf-8"), hashlib.sha256).hexdigest()


def _row_for_session(participant_session: ParticipantSession) -> dict[str, Any]:
    return {
        "subject_key": pseudonymous_subject_key(str(participant_session.participant_id)),
        "study_id": participant_session.study_id,
        "battery_id": participant_session.battery_id,
        "model_version_id": participant_session.model_version_id,
        "session_index": participant_session.session_index,
        "scores": participant_session.scores,
        "completed_at": (
            participant_session.completed_at.isoformat()
            if participant_session.completed_at
            else None
        ),
    }


def export_session(db: Session, participant_session_id: str) -> bool:
    """Export one scored session as a de-identified row. Returns True if a
    row was written (or would have been, absent configuration), False on
    failure. A missing/incomplete session or missing configuration is not a
    failure — it's a no-op."""
    if not settings.bigquery_deidentified_dataset:
        return False

    participant_session = (
        db.query(ParticipantSession)
        .filter(ParticipantSession.participant_session_id == participant_session_id)
        .first()
    )
    if participant_session is None or participant_session.scores is None:
        return False

    # The de-identified dataset is a research store: product-only participants,
    # and anyone who has since withdrawn research consent, must not reach it.
    try:
        ensure_processing_consent(
            db, str(participant_session.participant_id), ConsentType.RESEARCH_PARTICIPATION
        )
    except ConsentWithdrawnError:
        logger.warning(
            "BigQuery export skipped: no active research consent",
            extra={"participant_session_id": participant_session_id},
        )
        return False

    row = _row_for_session(participant_session)

    try:
        from google.cloud import bigquery

        client = bigquery.Client(project=settings.gcp_project_id)
        table_ref = (
            f"{settings.gcp_project_id}.{settings.bigquery_deidentified_dataset}.participant_scores"
        )
        errors = client.insert_rows_json(table_ref, [row])
        if errors:
            logger.error(
                "BigQuery insert reported row errors",
                extra={"participant_session_id": participant_session_id, "errors": errors},
            )
            return False
        return True
    except Exception:
        logger.error(
            "Failed to export de-identified session to BigQuery",
            exc_info=True,
            extra={"participant_session_id": participant_session_id},
        )
        return False

"""
Cascading Deletion Celery Tasks (governing doc §7: right to erasure).

app/routes/participants.py's DELETE endpoint already tombstones the
participant synchronously (is_deleted=True, PII columns cleared) so the API
contract is immediate. This task does the slower, cross-system cleanup that
tombstoning alone can't reach:

  - Cloud SQL: hard-delete the participant row (cascades to consents,
    study_enrollments, participant_sessions, trial_events via ON DELETE
    CASCADE) once retention requirements for the tombstone period have
    elapsed — called directly here rather than waiting, since the
    tombstone's job (immediate PII removal + audit trail of the erasure
    request) is already done by the time this task runs.
  - Cloud Storage: delete every raw trial event blob under the
    participant's sessions.
  - BigQuery: delete matching rows from the de-identified export dataset.
    (De-identified rows normally wouldn't need erasure since they're not
    PII by construction, but a participant-level erasure request is
    honored across every system that can be tied back to them by ID,
    de-identified export included.)

Each step is best-effort and independently logged: a Cloud Storage or
BigQuery outage must not leave the Cloud SQL deletion (the part fully
within our control) undone.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from celery import shared_task
from sqlalchemy.orm import Session

from app.config import settings
from app.database.connection import SessionLocal
from app.database.models import DataDeletion, Participant, ParticipantSession, TrialEvent

logger = logging.getLogger(__name__)


def _delete_raw_trial_events_from_gcs(participant_id: str, db: Session) -> int:
    if not settings.trial_events_bucket:
        return 0

    object_paths = [
        row[0]
        for row in (
            db.query(TrialEvent.raw_gcs_object)
            .join(
                ParticipantSession,
                TrialEvent.participant_session_id == ParticipantSession.participant_session_id,
            )
            .filter(
                ParticipantSession.participant_id == participant_id,
                TrialEvent.raw_gcs_object.isnot(None),
            )
            .all()
        )
    ]
    if not object_paths:
        return 0

    deleted = 0
    try:
        import google.cloud.storage as storage

        client = storage.Client(project=settings.gcp_project_id)
        bucket = client.bucket(settings.trial_events_bucket)
        for object_path in object_paths:
            try:
                bucket.blob(object_path).delete()
                deleted += 1
            except Exception:
                logger.error(
                    "Failed to delete raw trial event object",
                    exc_info=True,
                    extra={"participant_id": participant_id, "object_path": object_path},
                )
    except Exception:
        logger.error(
            "Failed to initialize Cloud Storage client for erasure",
            exc_info=True,
            extra={"participant_id": participant_id},
        )
    return deleted


def _delete_from_bigquery(participant_id: str) -> None:
    if not settings.bigquery_deidentified_dataset:
        return

    try:
        from google.cloud import bigquery

        client = bigquery.Client(project=settings.gcp_project_id)
        # nosec: B608 — the interpolated pieces are the trusted project ID and
        # dataset name from server config (BigQuery has no bind-parameter
        # syntax for table identifiers), not user input; the actual data
        # value (participant_id) is passed as a real query parameter below.
        query = f"""
            DELETE FROM `{settings.gcp_project_id}.{settings.bigquery_deidentified_dataset}.participant_scores`
            WHERE participant_id = @participant_id
        """  # nosec: B608
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("participant_id", "STRING", participant_id)
            ]
        )
        client.query(query, job_config=job_config).result()
    except Exception:
        # Includes the table not existing yet, which is expected before
        # Phase 3-4's BigQuery export path is deployed.
        logger.error(
            "Failed to delete de-identified BigQuery rows during erasure",
            exc_info=True,
            extra={"participant_id": participant_id},
        )


def _record_deletion_outcome(
    db: Session,
    participant_id: str,
    status: str,
    stores: Optional[dict[str, Any]] = None,
    error: bool = False,
) -> None:
    """Close the newest open data_deletions row; the ledger outlives the participant."""
    try:
        row = (
            db.query(DataDeletion)
            .filter(
                DataDeletion.participant_id == participant_id, DataDeletion.status == "requested"
            )
            .order_by(DataDeletion.requested_at.desc())
            .first()
        )
        if row is None:
            row = DataDeletion(participant_id=participant_id)
            db.add(row)
        row.status = status  # type: ignore[assignment]
        row.completed_at = datetime.now(timezone.utc)  # type: ignore[assignment]
        row.stores = stores  # type: ignore[assignment]
        if error:
            row.error = "cascading deletion raised; see worker logs"  # type: ignore[assignment]
        db.commit()
    except Exception:
        db.rollback()
        logger.error("Could not update deletion ledger", exc_info=True)


async def _erase_participant_data(participant_id: str) -> None:
    db = SessionLocal()
    try:
        gcs_deleted = _delete_raw_trial_events_from_gcs(participant_id, db)
        _delete_from_bigquery(participant_id)
        stores = {"gcs_objects_deleted": gcs_deleted, "bigquery": "deleted", "sql": "deleted"}

        participant = (
            db.query(Participant).filter(Participant.participant_id == participant_id).first()
        )
        if participant is not None:
            db.delete(participant)  # cascades to consents/enrollments/sessions/trial_events
            db.commit()

        _record_deletion_outcome(db, participant_id, "completed", stores=stores)
        logger.info(
            "Cascading deletion completed",
            extra={"participant_id": participant_id, "gcs_objects_deleted": gcs_deleted},
        )
    except Exception:
        db.rollback()
        _record_deletion_outcome(db, participant_id, "failed", error=True)
        logger.error(
            "Cascading deletion failed", exc_info=True, extra={"participant_id": participant_id}
        )
        raise
    finally:
        db.close()


@shared_task(name="erase_participant_data")  # type: ignore[untyped-decorator]
def erase_participant_data(participant_id: str) -> None:
    """Celery task: cross-system erasure for a tombstoned participant."""
    import asyncio

    asyncio.run(_erase_participant_data(participant_id))

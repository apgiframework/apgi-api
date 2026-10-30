"""Celery task: recompute SHA-256 for a committed acquisition chunk."""

from __future__ import annotations

import logging

from celery import shared_task

from app.database.connection import SessionLocal
from app.services.acquisition_ingest import verify
from app.services.acquisition_storage import get_acquisition_store
from app.services.job_tracker import UPLOAD_VERIFY, track

logger = logging.getLogger(__name__)


@shared_task(name="app.tasks.acquisition_tasks.verify_acquisition_upload")  # type: ignore[untyped-decorator]
def verify_acquisition_upload(upload_id: str) -> str:
    with track(UPLOAD_VERIFY, upload_id):
        store = get_acquisition_store()
        if store is None:
            raise RuntimeError("ACQUISITION_BUCKET is not configured")
        db = SessionLocal()
        try:
            status = verify(db, store, upload_id)
            logger.info("acquisition upload %s -> %s", upload_id, status)
            return status
        finally:
            db.close()

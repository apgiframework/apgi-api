"""Watchdog for durable job records (S2-CAT-05): runs independently of the workers."""

from __future__ import annotations

import logging
from typing import Dict

from celery import shared_task

from app.database.connection import SessionLocal
from app.services.job_tracker import sweep

logger = logging.getLogger(__name__)


@shared_task(name="ops.sweep_jobs")  # type: ignore[untyped-decorator]
def sweep_jobs() -> Dict[str, int]:
    db = SessionLocal()
    try:
        counts = sweep(db)
        if counts["failed"] or counts["dead"]:
            logger.error("job watchdog recorded lost jobs", extra=counts)
        return counts
    finally:
        db.close()


@shared_task(name="ops.evaluate_alerts")  # type: ignore[untyped-decorator]
def evaluate_alerts() -> Dict[str, int]:
    from app.services.ops_alerts import run_cycle

    db = SessionLocal()
    try:
        return run_cycle(db)
    finally:
        db.close()

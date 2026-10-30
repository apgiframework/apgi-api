"""Replay the erasure ledger after a backup restore (S2-DEL-01.10).

A restored database can resurrect participants who were erased after the
backup was taken. Every `data_deletions` row marks an erasure that must
stay true, so replay re-erases any participant it names that has come back.
The ledger itself must be restored from (or replayed against) a store that
survives the restore; this function is the idempotent re-application step.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.database.models import DataDeletion, Participant


def replay_erasure_ledger(db: Session, participant_ids: Optional[list[str]] = None) -> list[str]:
    """Re-erase resurrected participants; returns the participant ids removed."""
    query = db.query(DataDeletion.participant_id).filter(DataDeletion.status != "failed")
    if participant_ids is not None:
        query = query.filter(DataDeletion.participant_id.in_(participant_ids))
    erased_ids = {row[0] for row in query.distinct()}
    resurrected = (
        db.query(Participant).filter(Participant.participant_id.in_(erased_ids)).all()
        if erased_ids
        else []
    )
    removed = [str(p.participant_id) for p in resurrected]
    for participant in resurrected:
        db.delete(participant)  # cascades to consents, sessions, trial events, item responses
    if resurrected:
        db.commit()
    return removed

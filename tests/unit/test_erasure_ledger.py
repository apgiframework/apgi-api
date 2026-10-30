"""Erasure ledger replay after a restore leaves no resurrected participant."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import Base, DataDeletion, Participant
from app.services.erasure_replay import replay_erasure_ledger


def _db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_replay_removes_participants_resurrected_by_a_restore():
    db = _db()
    erased, kept = Participant(), Participant()
    db.add_all([erased, kept])
    db.commit()
    # The ledger survived the restore; the participant row came back with the backup.
    db.add(DataDeletion(participant_id=erased.participant_id, status="completed"))
    db.commit()

    removed = replay_erasure_ledger(db)

    assert removed == [erased.participant_id]
    remaining = {p.participant_id for p in db.query(Participant)}
    assert remaining == {kept.participant_id}
    assert replay_erasure_ledger(db) == []  # idempotent


def test_failed_deletions_are_not_replayed_as_erased():
    db = _db()
    p = Participant()
    db.add(p)
    db.commit()
    db.add(DataDeletion(participant_id=p.participant_id, status="failed"))
    db.commit()
    assert replay_erasure_ledger(db) == []

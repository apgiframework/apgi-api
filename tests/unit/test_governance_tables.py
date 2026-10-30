"""Append-only consent ledger, holdout immutability, deletion ledger (S2-SCH-21/23.8, S2-DEL-01)."""

from typing import Generator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session, sessionmaker

from app.database.models import (
    Base,
    ConsentRecord,
    ConsentType,
    Participant,
    Study,
    StudyEnrollment,
)
from app.services.consent import record_consent, revoke_consent


@pytest.fixture
def db() -> Generator[Session, None, None]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _participant(db: Session) -> Participant:
    p = Participant()
    db.add(p)
    db.commit()
    return p


def test_grant_and_revoke_append_ledger_rows(db: Session) -> None:
    p = _participant(db)
    record_consent(db, p.participant_id, ConsentType.RECONTACT, "text")
    record_consent(db, p.participant_id, ConsentType.RECONTACT, "text")
    assert revoke_consent(db, p.participant_id, ConsentType.RECONTACT) == 2
    actions = [r.action for r in db.query(ConsentRecord).order_by(ConsentRecord.recorded_at)]
    assert sorted(actions) == ["grant", "grant", "withdraw"]


def test_consent_records_reject_update_and_delete(db: Session) -> None:
    p = _participant(db)
    record_consent(db, p.participant_id, ConsentType.RECONTACT, "text")
    with pytest.raises(DatabaseError):
        db.execute(text("UPDATE consent_records SET action='withdraw'"))
    db.rollback()
    with pytest.raises(DatabaseError):
        db.execute(text("DELETE FROM consent_records"))


def test_holdout_partition_is_immutable_once_set(db: Session) -> None:
    p = _participant(db)
    study = Study(name="s")
    db.add(study)
    db.commit()
    enr = StudyEnrollment(participant_id=p.participant_id, study_id=study.study_id)
    db.add(enr)
    db.commit()
    enr.partition = "holdout"
    db.commit()  # first assignment (NULL -> value) is allowed
    enr.partition = "development"
    with pytest.raises(DatabaseError):
        db.commit()
    db.rollback()
    enr.partition = "holdout"  # same value is not a change
    db.commit()

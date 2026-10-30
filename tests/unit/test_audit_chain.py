"""PR 0 acceptance: a score writes a verifiable, chained audit record; tampering breaks it."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import AuditLog, Base
from app.services.audit_signing import verify_chain
from app.services.authorization import log_audit_event


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[AuditLog.__table__])
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _rows(db):
    return db.query(AuditLog).order_by(AuditLog.chain_seq).all()


def _write(db, n=3):
    for i in range(n):
        log_audit_event(db, None, "score.generate", "session", f"s{i}", details={"i": i})


def test_chain_links_and_verifies(db):
    _write(db)
    rows = _rows(db)
    assert [r.chain_seq for r in rows] == [1, 2, 3]
    assert rows[0].prev_record_sha256 == "0" * 64
    assert all(r.signing_key_version == "v1" and r.signature for r in rows)
    assert verify_chain(rows) == (True, None)


def test_tampered_details_break_verification(db):
    _write(db)
    row = _rows(db)[1]
    row.details = {"i": 999}
    db.commit()
    assert verify_chain(_rows(db)) == (False, row.audit_id)


def test_deleted_record_breaks_chain(db):
    _write(db)
    rows = _rows(db)
    db.delete(rows[1])
    db.commit()
    assert verify_chain(_rows(db)) == (False, rows[2].audit_id)


def test_score_session_writes_verifiable_chained_record_and_tamper_fails(monkeypatch):
    """S0-PR0-06: a test score writes input hash, output hash and stamps into the chain."""
    from app.config import settings
    from app.database.models import (
        Battery,
        Participant,
        ParticipantSession,
        Study,
        TrialEvent,
    )
    from app.services.scoring import score_session

    monkeypatch.setattr(settings, "audit_signing_key", "k" * 40)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    participant, study = Participant(), Study(name="s")
    db.add_all([participant, study])
    db.commit()
    battery = Battery(study_id=study.study_id, name="b", version="1.0", form_label="A")
    db.add(battery)
    db.commit()
    session = ParticipantSession(
        participant_id=participant.participant_id,
        study_id=study.study_id,
        battery_id=battery.battery_id,
    )
    db.add(session)
    db.commit()
    db.add(
        TrialEvent(
            participant_session_id=session.participant_session_id,
            task_type="stroop",
            trial_index=0,
            correct=True,
            rt_ms=400.0,
        )
    )
    db.commit()

    score_session(db, session.participant_session_id)

    rows = _rows(db)
    assert [r.action for r in rows] == ["score.generate"]
    d = rows[0].details
    assert len(d["input_sha256"]) == 64 and len(d["output_sha256"]) == 64
    assert d["stamps"]["platform_release"] and d["stamps"]["gate_table_version"]
    assert rows[0].signing_key_version and verify_chain(rows) == (True, None)

    rows[0].details = {**d, "output_sha256": "0" * 64}
    db.commit()
    assert verify_chain(_rows(db)) == (False, rows[0].audit_id)


def test_verify_audit_chain_cli(db, monkeypatch):
    from click.testing import CliRunner

    import app.database.connection as connection
    from app.cli import cli

    _write(db)
    monkeypatch.setattr(connection, "SessionLocal", lambda: db)
    result = CliRunner().invoke(cli, ["verify-audit-chain"])
    assert result.exit_code == 0 and "3 chained records" in result.output

    row = _rows(db)[0]
    row.details = {"i": 42}
    db.commit()
    result = CliRunner().invoke(cli, ["verify-audit-chain"])
    assert result.exit_code == 1


def _scored_session_db():
    from app.database.models import Battery, Participant, Study

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    participant, study = Participant(), Study(name="s")
    db.add_all([participant, study])
    db.commit()
    battery = Battery(study_id=study.study_id, name="b", version="1.0", form_label="A")
    db.add(battery)
    db.commit()
    return db, participant, study, battery


def test_scoring_uses_the_model_bound_at_authorisation_not_the_active_one():
    from app.database.models import ModelVersion, ParticipantSession
    from app.services.scoring import score_session

    db, participant, study, battery = _scored_session_db()
    old = ModelVersion(version="1.0", is_active=True)
    db.add(old)
    db.commit()
    session = ParticipantSession(
        participant_id=participant.participant_id,
        study_id=study.study_id,
        battery_id=battery.battery_id,
        model_version_id=old.model_version_id,  # bound when processing was authorised
    )
    db.add(session)
    old.is_active = False
    db.add(ModelVersion(version="2.0", is_active=True))  # newer model activated afterwards
    db.commit()

    scored = score_session(db, session.participant_session_id)
    assert scored.scores["stamps"]["model_version"] == "1.0"
    assert scored.model_version_id == old.model_version_id


def test_scoring_fails_when_the_bound_model_is_gone():
    import pytest

    from app.database.models import ParticipantSession
    from app.services.scoring import ModelBindingError, score_session

    db, participant, study, battery = _scored_session_db()
    db.execute(__import__("sqlalchemy").text("PRAGMA foreign_keys=OFF"))
    session = ParticipantSession(
        participant_id=participant.participant_id,
        study_id=study.study_id,
        battery_id=battery.battery_id,
        model_version_id="missing-model",
    )
    db.add(session)
    db.commit()
    with pytest.raises(ModelBindingError):
        score_session(db, session.participant_session_id)

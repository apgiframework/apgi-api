"""PR 0 acceptance: a score writes a verifiable, chained audit record; tampering breaks it."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.models import AuditChainHead, AuditLog, Base
from app.services.audit_signing import verify_chain
from app.services.authorization import log_audit_event


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[AuditLog.__table__, AuditChainHead.__table__])
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
    _grant_consent(db, participant)
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


def _grant_consent(db, participant):
    from app.config import settings
    from app.database.models import Consent, ConsentType

    db.add(
        Consent(
            participant_id=participant.participant_id,
            consent_type=ConsentType.RESEARCH_PARTICIPATION,
            version=settings.consent_current_versions[ConsentType.RESEARCH_PARTICIPATION.value],
            consent_text_hash="0" * 64,
        )
    )
    db.commit()


def _scored_session_db():
    from app.database.models import Battery, Participant, Study

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    participant, study = Participant(), Study(name="s")
    db.add_all([participant, study])
    db.commit()
    _grant_consent(db, participant)
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


# --- DEF-05 / DEF-06 / S0-PR0-03: bound metadata, historical keys, serialised head -----


def test_tampered_timestamp_key_version_or_seq_is_detected(db):
    from datetime import timedelta

    _write(db)
    for field, value in (
        ("timestamp", lambda r: r.timestamp + timedelta(days=1)),
        ("signing_key_version", lambda r: "v9"),
        ("actor_id", lambda r: "someone-else"),
    ):
        row = _rows(db)[1]
        original = getattr(row, field)
        setattr(row, field, value(row))
        db.commit()
        assert verify_chain(_rows(db)) == (False, row.audit_id), field
        setattr(row, field, original)
        db.commit()
    assert verify_chain(_rows(db)) == (True, None)


def test_user_erasure_nulling_user_id_keeps_chain_valid(db):
    log_audit_event(db, "user-1", "login", "user", "user-1")
    row = _rows(db)[0]
    row.user_id = None  # what ON DELETE SET NULL does
    db.commit()
    assert verify_chain(_rows(db)) == (True, None)


def test_rotated_key_verifies_old_rows_with_their_own_key(db, monkeypatch):
    import app.services.audit_signing as signing
    import app.services.authorization as authz

    _write(db, 2)
    old_key = signing.settings.audit_signing_key
    # Other tests may reload app.config; patch the object each module actually uses.
    for st in {id(signing.settings): signing.settings, id(authz.settings): authz.settings}.values():
        monkeypatch.setattr(st, "audit_signing_key", "n" * 40)
        monkeypatch.setattr(st, "audit_signing_key_version", "v2")
        monkeypatch.setattr(st, "audit_signing_keys", {"v1": old_key}, raising=False)
    settings = signing.settings
    log_audit_event(db, None, "score.generate", "session", "after-rotation")
    rows = _rows(db)
    assert [r.signing_key_version for r in rows] == ["v1", "v1", "v2"]
    assert verify_chain(rows) == (True, None)
    # Without the retired key the old rows can no longer be verified.
    monkeypatch.setattr(signing.settings, "audit_signing_keys", {})
    assert verify_chain(_rows(db))[0] is False


def test_missing_signing_key_refuses_the_write(db, monkeypatch):
    import app.services.audit_signing as signing
    import app.services.authorization as authz
    from app.services.audit_signing import AuditSigningNotConfiguredError

    for st in {id(signing.settings): signing.settings, id(authz.settings): authz.settings}.values():
        monkeypatch.setattr(st, "audit_signing_key", None)
    with pytest.raises(AuditSigningNotConfiguredError):
        log_audit_event(db, None, "x", "y", "z")
    assert db.query(AuditLog).count() == 0


def test_first_append_goes_through_the_chain_head(db):
    assert db.query(AuditChainHead).count() == 0
    log_audit_event(db, None, "a", "r", "1")
    head = db.query(AuditChainHead).one()
    assert head.last_seq == 1
    log_audit_event(db, None, "a", "r", "2")
    assert db.query(AuditChainHead).one().last_seq == 2
    assert verify_chain(_rows(db)) == (True, None)


def test_sequence_gap_is_detected(db):
    _write(db, 3)
    db.delete(_rows(db)[0])
    db.commit()
    assert verify_chain(_rows(db))[0] is False


def test_scoring_is_refused_when_consent_was_never_granted_or_since_withdrawn():
    from app.database.models import ConsentType, ParticipantSession
    from app.services.consent import revoke_consent
    from app.services.scoring import score_session

    db, participant, study, battery = _scored_session_db()
    session = ParticipantSession(
        participant_id=participant.participant_id,
        study_id=study.study_id,
        battery_id=battery.battery_id,
    )
    db.add(session)
    db.commit()
    for purpose in (ConsentType.PRODUCT_USE, ConsentType.RESEARCH_PARTICIPATION):
        revoke_consent(db, participant.participant_id, purpose)  # withdrawn after ingestion
    assert score_session(db, session.participant_session_id) is None  # discarded, not stored
    assert db.query(AuditLog).count() == 0  # nothing scored, nothing audited as scored


def test_research_export_requires_active_research_consent(monkeypatch):
    from app.config import settings
    from app.database.models import ParticipantSession
    from app.services import bigquery_export

    db, participant, study, battery = _scored_session_db()  # product-use consent only
    session = ParticipantSession(
        participant_id=participant.participant_id,
        study_id=study.study_id,
        battery_id=battery.battery_id,
        scores={"tasks": {}},
    )
    db.add(session)
    db.commit()
    monkeypatch.setattr(settings, "bigquery_deidentified_dataset", "ds", raising=False)
    assert bigquery_export.export_session(db, session.participant_session_id) is False

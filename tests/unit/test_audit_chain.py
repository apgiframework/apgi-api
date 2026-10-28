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

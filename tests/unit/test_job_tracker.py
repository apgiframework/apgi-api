"""Durable job tracking: enqueue records, lease expiry, dead-lettering, retry."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Generator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.connection import get_db
from app.database.models import Base, OpsJob
from app.main import create_app
from app.middleware.correlation import correlation_id_context
from app.routes import ops
from app.services import job_tracker, ops_monitor
from app.services.authorization import get_current_user


@pytest.fixture
def db_factory() -> Generator[Any, None, None]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine)
    engine.dispose()


@pytest.fixture
def db(db_factory: Any) -> Generator[Any, None, None]:
    s = db_factory()
    yield s
    s.close()


def _ago(**kw: Any) -> datetime:
    return datetime.now(timezone.utc) - timedelta(**kw)


def test_enqueue_is_idempotent_and_captures_correlation(db: Any) -> None:
    token = correlation_id_context.set("corr-abcdef12")
    try:
        a = job_tracker.record_enqueue(db, "scoring", "s1")
    finally:
        correlation_id_context.reset(token)
    b = job_tracker.record_enqueue(db, "scoring", "s1")
    assert a.job_id == b.job_id and db.query(OpsJob).count() == 1
    assert a.correlation_id == "corr-abcdef12"


def test_classify_stale_queued_and_lease_expired(db: Any) -> None:
    j = job_tracker.record_enqueue(db, "scoring", "s1")
    assert job_tracker.classify(j) == "queued"
    j.enqueued_at = _ago(hours=1)
    assert job_tracker.classify(j) == "stale_queued"
    j.status, j.lease_expires_at = "running", _ago(minutes=1)
    assert job_tracker.classify(j) == "lease_expired"


def test_sweep_fails_then_dead_letters_without_worker_write(db: Any) -> None:
    j = job_tracker.record_enqueue(db, "upload_verify", "u1")
    j.status, j.attempts, j.lease_expires_at = "running", 1, _ago(minutes=1)
    db.commit()
    assert job_tracker.sweep(db) == {"failed": 1, "dead": 0}
    db.refresh(j)
    assert j.status == "failed" and "lease expired" in j.error
    j.status, j.attempts, j.lease_expires_at = "running", 3, _ago(minutes=1)
    db.commit()
    assert job_tracker.sweep(db) == {"failed": 0, "dead": 1}


def test_track_marks_success_and_failure(db_factory: Any) -> None:
    with patch("app.database.connection.SessionLocal", db_factory):
        with job_tracker.track("scoring", "s1"):
            pass
        with pytest.raises(RuntimeError):
            with job_tracker.track("scoring", "s2"):
                raise RuntimeError("boom")
    s = db_factory()
    ok = s.query(OpsJob).filter_by(ref="s1").one()
    bad = s.query(OpsJob).filter_by(ref="s2").one()
    assert ok.status == "succeeded" and ok.attempts == 1 and ok.lease_expires_at is None
    assert bad.status == "failed" and "boom" in bad.error


def test_track_never_breaks_work_when_tracking_unavailable() -> None:
    with patch("app.database.connection.SessionLocal", side_effect=RuntimeError("db down")):
        ran = []
        with job_tracker.track("scoring", "s1"):
            ran.append(1)
    assert ran == [1]


@pytest.fixture
def client(db: Any) -> Generator[TestClient, None, None]:
    app = create_app(test_mode=True)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(user_id="admin-1")
    for guard in ops.GUARDS:
        app.dependency_overrides[guard] = lambda: None
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_failures_group_by_job_state_and_retry(client: TestClient, db: Any) -> None:
    lost = job_tracker.record_enqueue(db, "scoring", "s-lost")
    lost.status, lost.lease_expires_at = "running", _ago(minutes=5)
    dead = job_tracker.record_enqueue(db, "upload_verify", "u-dead")
    dead.status, dead.error = "dead", "boom"
    ok = job_tracker.record_enqueue(db, "scoring", "s-ok")
    ok.status = "succeeded"
    db.commit()

    rows = client.get("/v1/ops/failures").json()["data"]
    groups = {r["ref"]: r["group"] for r in rows}
    assert groups == {
        "scoring:s-lost": "Worker lost (lease expired)",
        "upload_verify:u-dead": "Dead-lettered jobs (retries exhausted)",
    }
    assert all(r["retry_eligible"] for r in rows)

    home = client.get("/v1/ops/home").json()["data"]["jobs"]["durable_jobs"]
    assert home["lease_expired"] == 1 and home["dead"] == 1

    with patch("app.tasks.scoring_tasks.score_participant_session.delay", create=True) as delay:
        r = client.post(f"/v1/ops/jobs/job:{lost.job_id}:retry")
    assert r.status_code == 200
    delay.assert_called_once_with("s-lost")
    db.refresh(lost)
    assert lost.status == "queued"
    assert client.post(f"/v1/ops/jobs/job:{ok.job_id}:retry").status_code == 409


def test_retry_of_dead_job_restores_budget(db: Any) -> None:
    j = job_tracker.record_enqueue(db, "scoring", "s1")
    j.status, j.attempts = "dead", 3
    db.commit()
    again = job_tracker.record_enqueue(db, "scoring", "s1")
    assert again.job_id == j.job_id and again.status == "queued" and again.attempts == 0


def test_untracked_legacy_rows_still_surface_once(db: Any) -> None:
    assert ops_monitor.collect_failures(db) == []

"""Unit tests for the /v1/ops read API and the /console static host."""

from datetime import datetime, timedelta, timezone
from typing import Any, Generator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.connection import get_db
from app.database.models import AcquisitionUpload, Base
from app.main import create_app
from app.routes import ops
from app.services import ops_monitor


@pytest.fixture
def test_db_session() -> Generator[Any, None, None]:
    """In-memory SQLite shared across connections (StaticPool) with every table created."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def client(test_db_session: Any) -> Generator[TestClient, None, None]:
    app = create_app(test_mode=True)
    app.dependency_overrides[get_db] = lambda: test_db_session
    for dep in ops.router.dependencies:
        app.dependency_overrides[dep.dependency] = lambda: None
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _upload(db: Any, **kw: Any) -> AcquisitionUpload:
    base = dict(
        session_id="s1",
        object_name="o",
        size_bytes=1,
        crc32c=1,
        sha256="a" * 64,
        sequence=0,
        principal="p",
        status="pending",
        reason="",
        committed=True,
    )
    base.update(kw)
    rec = AcquisitionUpload(**base)
    db.add(rec)
    db.commit()
    return rec


def test_empty_database_reports_unknown_not_zero(client: TestClient) -> None:
    data = client.get("/v1/ops/home").json()["data"]
    assert [f["state"] for f in data["freshness"]] == ["unknown"] * 4
    assert data["quality"]["completion"]["completion_rate"] is None
    assert data["infrastructure"]["availability"] is None
    assert data["origin"]["synthetic"] == 0 and data["origin"]["unclassified"] == 0


def test_envelope_lists_as_of_per_source(client: TestClient) -> None:
    body = client.get("/v1/ops/home").json()
    assert set(body["as_of"]) == set(ops_monitor.SOURCE_INTERVALS)


def test_failed_task_listed_and_stuck_upload_retryable(
    client: TestClient, test_db_session: Any
) -> None:
    old = datetime.now(timezone.utc) - timedelta(hours=1)
    up = _upload(test_db_session, updated_at=old)
    failures = client.get("/v1/ops/failures").json()["data"]
    stuck = [f for f in failures if f["job_id"] == f"upload:{up.upload_id}"]
    assert stuck and stuck[0]["retry_eligible"] is True and stuck[0]["correlation_id"] is None


def test_quarantined_upload_appears_not_retryable(client: TestClient, test_db_session: Any) -> None:
    up = _upload(test_db_session, status="quarantined", reason="sha256 mismatch")
    f = next(x for x in client.get("/v1/ops/failures").json()["data"] if x["ref"] == up.upload_id)
    assert f["retry_eligible"] is False and "sha256 mismatch" in f["error"]


def test_retry_requeues_eligible_upload(client: TestClient, test_db_session: Any) -> None:
    up = _upload(test_db_session)
    with patch("app.tasks.acquisition_tasks.verify_acquisition_upload.delay", create=True) as delay:
        r = client.post(f"/v1/ops/jobs/upload:{up.upload_id}:retry")
    assert r.status_code == 200
    delay.assert_called_once_with(up.upload_id)


def test_retry_rejects_ineligible_and_unknown(client: TestClient, test_db_session: Any) -> None:
    up = _upload(test_db_session, status="accepted")
    assert client.post(f"/v1/ops/jobs/upload:{up.upload_id}:retry").status_code == 409
    assert client.post("/v1/ops/jobs/upload:missing:retry").status_code == 404
    assert client.post("/v1/ops/jobs/webhook:x:retry").status_code == 409


def test_catalog_search_and_filter(client: TestClient, test_db_session: Any) -> None:
    _upload(test_db_session, status="accepted")
    _upload(test_db_session, status="rejected", sequence=1)
    res = client.get("/v1/ops/catalog", params={"kind": "upload", "status": "rejected"}).json()[
        "data"
    ]
    assert res["total"] == 1 and res["items"][0]["origin"] is None
    assert client.get("/v1/ops/catalog", params={"kind": "bogus"}).status_code == 422


def test_session_detail_404(client: TestClient) -> None:
    assert client.get("/v1/ops/catalog/session/nope").status_code == 404


def test_ops_requires_authentication() -> None:
    app = create_app(test_mode=True)
    with TestClient(app, raise_server_exceptions=False) as c:
        assert c.get("/v1/ops/home").status_code in (401, 403)


def test_console_shell_served_publicly() -> None:
    app = create_app(test_mode=True)
    with TestClient(app) as c:
        assert "APGI Operations Console" in c.get("/console").text
        assert c.get("/console/console.js").status_code == 200
        assert c.get("/console/console.css").status_code == 200


def test_helpers() -> None:
    assert ops_monitor.aware(None) is None
    assert ops_monitor.age_seconds(None) is None
    assert ops_monitor.aware(datetime(2026, 1, 1)).tzinfo is not None


# ---------------------------------------------------------------------------
# Data origin and correlation IDs
# ---------------------------------------------------------------------------


def _session(db: Any, origin: Any, status: Any = None) -> Any:
    from app.database.models import (
        Battery,
        Participant,
        ParticipantSession,
        ParticipantSessionStatus,
        Study,
    )

    study = Study(name="s")
    db.add(study)
    db.commit()
    battery = Battery(study_id=study.study_id, name="b", version="1", instrument_schema={})
    part = Participant()
    db.add_all([battery, part])
    db.commit()
    ps = ParticipantSession(
        participant_id=part.participant_id,
        study_id=study.study_id,
        battery_id=battery.battery_id,
        status=status or ParticipantSessionStatus.COMPLETED,
        data_origin=origin,
        correlation_id="corr-12345678",
    )
    db.add(ps)
    db.commit()
    return ps


def test_origin_panel_counts_and_filters(client: TestClient, test_db_session: Any) -> None:
    _session(test_db_session, "synthetic")
    _session(test_db_session, "human_observed")
    _session(test_db_session, None)
    home = client.get("/v1/ops/home").json()["data"]
    assert (
        home["origin"]["synthetic"],
        home["origin"]["human_observed"],
        home["origin"]["unclassified"],
    ) == (1, 1, 1)
    only = client.get("/v1/ops/home", params={"origin": "synthetic"}).json()["data"]
    assert only["quality"]["completion"]["total"] == 1
    assert client.get("/v1/ops/home", params={"origin": "bogus"}).status_code == 422


def test_catalog_origin_filter_and_detail(client: TestClient, test_db_session: Any) -> None:
    ps = _session(test_db_session, "synthetic")
    _session(test_db_session, None)
    res = client.get("/v1/ops/catalog", params={"origin": "unclassified"}).json()["data"]
    assert res["total"] == 1 and res["items"][0]["origin"] is None
    detail = client.get(f"/v1/ops/catalog/session/{ps.participant_session_id}").json()["data"]
    assert detail["origin"] == "synthetic" and detail["correlation_id"] == "corr-12345678"


def test_classify_session_records_origin(client: TestClient, test_db_session: Any) -> None:
    from types import SimpleNamespace

    from app.services.authorization import get_current_user

    client.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(user_id="admin-1")  # type: ignore[attr-defined]
    ps = _session(test_db_session, None)
    with patch("app.routes.ops.log_audit_event") as audit:
        r = client.post(
            f"/v1/ops/sessions/{ps.participant_session_id}:classify", json={"origin": "synthetic"}
        )
    assert r.status_code == 200 and r.json()["data"]["previous"] is None
    audit.assert_called_once()
    test_db_session.refresh(ps)
    assert ps.data_origin == "synthetic"
    assert (
        client.post("/v1/ops/sessions/x:classify", json={"origin": "synthetic"}).status_code == 404
    )
    assert (
        client.post(
            f"/v1/ops/sessions/{ps.participant_session_id}:classify", json={"origin": "maybe"}
        ).status_code
        == 422
    )


def test_correlation_id_minted_echoed_and_sanitised() -> None:
    app = create_app(test_mode=True)
    with TestClient(app) as c:
        minted = c.get("/health/live").headers["X-APGI-Correlation-Id"]
        assert len(minted) == 36
        ok = c.get("/health/live", headers={"X-APGI-Correlation-Id": "trace-abcdef12"})
        assert ok.headers["X-APGI-Correlation-Id"] == "trace-abcdef12"
        bad = c.get("/health/live", headers={"X-APGI-Correlation-Id": 'x"; drop'})
        assert bad.headers["X-APGI-Correlation-Id"] != 'x"; drop'

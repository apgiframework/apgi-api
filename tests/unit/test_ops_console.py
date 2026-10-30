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


@pytest.fixture(autouse=True)
def _no_live_probes() -> Generator[None, None, None]:
    """Platform health would otherwise probe the real DB/broker/GCP."""
    from app.services import platform_health

    empty = {
        "success_ratio_1h": None,
        "error_ratio_5xx": None,
        "p95_latency_ms": None,
        "request_count_1h": None,
        "last_backup_at": None,
        "last_restore_drill_at": None,
        "database_latency_ms": None,
        "celery_workers_online": None,
        "configured": {},
        "errors": [],
        "source": "",
        "as_of": {"local_probes": None},
    }
    with patch.object(platform_health, "snapshot", lambda force=False: empty):
        yield


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
    assert data["infrastructure"]["success_ratio_1h"] is None
    assert data["origin"]["synthetic"] == 0 and data["origin"]["unclassified"] == 0


def test_envelope_lists_as_of_per_source(client: TestClient) -> None:
    body = client.get("/v1/ops/home").json()
    assert set(ops_monitor.SOURCE_INTERVALS) <= set(body["as_of"])


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


# ---------------------------------------------------------------------------
# Data-quality indicators
# ---------------------------------------------------------------------------


def _quality_world(db: Any) -> Any:
    from app.database.models import (
        Battery,
        Participant,
        ParticipantSession,
        ParticipantSessionStatus,
        Study,
        StudyEnrollment,
        TrialEvent,
    )

    study = Study(name="Pilot")
    db.add(study)
    db.commit()
    battery = Battery(study_id=study.study_id, name="Explorer", version="1.0", instrument_schema={})
    people = [Participant() for _ in range(3)]
    db.add_all([battery, *people])
    db.commit()

    def add(p: Any, idx: int, status: Any, **kw: Any) -> Any:
        s = ParticipantSession(
            participant_id=p.participant_id,
            study_id=study.study_id,
            battery_id=battery.battery_id,
            session_index=idx,
            status=status,
            **kw,
        )
        db.add(s)
        db.commit()
        return s

    C = ParticipantSessionStatus
    a = add(
        people[0],
        1,
        C.COMPLETED,
        device_tier="A",
        data_origin="synthetic",
        administration_mode="web",
        app_version="1.2.0",
        browser_family="Firefox",
        norm_eligible=True,
        screening={"flags": [], "exclusion_reasons": []},
        timing_error_distribution={"n": 10, "p95": 4.0},
        scores={"x": 1},
    )
    add(
        people[0],
        2,
        C.COMPLETED,
        device_tier="A",
        data_origin="synthetic",
        administration_mode="web",
        app_version="1.2.0",
    )
    add(
        people[1],
        1,
        C.COMPLETED,
        device_tier="C",
        data_origin="synthetic",
        administration_mode="native_macos",
        app_version="2.0.0",
        norm_eligible=False,
        screening={"flags": [], "exclusion_reasons": ["device_tier_c"]},
        timing_error_distribution={"n": 10, "p95": 30.0},
    )
    add(people[2], 1, C.ABANDONED, data_origin="human_observed")
    for seq in (0, 1, 3):  # gap at 2
        db.add(
            TrialEvent(
                participant_session_id=a.participant_session_id,
                task_type="t",
                trial_index=seq,
                seq_no=seq,
            )
        )
    db.add(
        StudyEnrollment(
            participant_id=people[0].participant_id, study_id=study.study_id, partition="holdout"
        )
    )
    db.add(StudyEnrollment(participant_id=people[1].participant_id, study_id=study.study_id))
    db.commit()
    return study, battery


def _by_key(res: Any) -> Any:
    return {i["key"]: i for i in res["data"]["indicators"]}


def test_fourteen_indicators_with_honest_status(client: TestClient, test_db_session: Any) -> None:
    _quality_world(test_db_session)
    res = client.get("/v1/ops/data-quality").json()
    ind = _by_key(res)
    assert len(res["data"]["indicators"]) == 14 and res["data"]["planned"] == 14
    for key in (
        "kgate_classifications",
        "reproduction",
    ):
        assert ind[key]["status"] == "unmeasured" and ind[key]["value"] is None and ind[key]["note"]
    assert (
        ind["completion"]["value"]["completed"] == 3
        and ind["completion"]["value"]["abandoned"] == 1
    )
    assert ind["missing_events"]["value"]["missing_events"] == 1
    assert ind["exclusions"]["value"]["reasons"] == {"device_tier_c": 1}
    assert ind["exclusions"]["value"]["unscreened"] == 2
    assert ind["clock_sync"]["value"]["worst_session_p95_error_ms"] == 30.0
    assert ind["analysis_status"]["value"]["awaiting_scoring"] == 2
    assert ind["retention"]["value"]["participants_completing_wave_1"] == 2
    assert ind["retention"]["value"]["waves"]["wave_2"]["participants"] == 1
    assert ind["device_tier"]["status"] == "partial"
    assert ind["device_tier"]["value"]["tier"] == {"A": 2, "C": 1, "unknown": 1}
    assert ind["device_tier"]["value"]["browser_family"] == {"Firefox": 1, "not reported": 3}
    assert ind["admin_mode_version"]["status"] == "measured"
    assert ind["admin_mode_version"]["value"]["administration_mode"] == {
        "web": 2,
        "native_macos": 1,
        "not reported": 1,
    }
    assert ind["admin_mode_version"]["value"]["app_version"]["1.2.0"] == 2
    hold = ind["holdout_separation"]["value"]
    assert hold["participants"] == {"holdout": 1, "unassigned": 2} and hold["sessions"] == {
        "holdout": 2,
        "unassigned": 2,
    }
    assert res["data"]["available"] == 12


def test_data_quality_filters_by_origin_and_has_no_identifiers(
    client: TestClient, test_db_session: Any
) -> None:
    study, battery = _quality_world(test_db_session)
    syn = client.get("/v1/ops/data-quality", params={"origin": "synthetic"}).json()
    assert syn["data"]["session_count"] == 3
    hum = client.get("/v1/ops/data-quality", params={"origin": "human_observed"}).json()
    assert _by_key(hum)["completion"]["value"]["interruption_rate"] == 1.0
    scoped = client.get("/v1/ops/data-quality", params={"study_id": "other"}).json()
    assert scoped["data"]["session_count"] == 0
    assert _by_key(scoped)["completion"]["value"]["completion_rate"] is None
    body = str(client.get("/v1/ops/data-quality").json())
    from app.database.models import Participant, ParticipantSession

    for pid in test_db_session.query(Participant.participant_id):
        assert pid[0] not in body
    for sid in test_db_session.query(ParticipantSession.participant_session_id):
        assert sid[0] not in body
    studies = client.get("/v1/ops/studies").json()["data"]
    assert (
        studies["studies"][0]["name"] == "Pilot"
        and studies["batteries"][0]["study_id"] == study.study_id
    )


def test_empty_database_quality_is_unknown_not_zero(client: TestClient) -> None:
    res = client.get("/v1/ops/data-quality").json()["data"]
    comp = next(i for i in res["indicators"] if i["key"] == "completion")
    assert comp["value"]["completion_rate"] is None and res["session_count"] == 0


def test_partition_assignment_is_set_once_and_audited(
    client: TestClient, test_db_session: Any
) -> None:
    from types import SimpleNamespace

    from app.database.models import StudyEnrollment
    from app.services.authorization import get_current_user

    client.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(user_id="admin-1")  # type: ignore[attr-defined]
    study, _ = _quality_world(test_db_session)
    enr = test_db_session.query(StudyEnrollment).filter(StudyEnrollment.partition.is_(None)).first()
    with patch("app.routes.ops.log_audit_event") as audit:
        ok = client.post(
            f"/v1/ops/enrollments/{enr.enrollment_id}:partition", json={"partition": "development"}
        )
        again = client.post(
            f"/v1/ops/enrollments/{enr.enrollment_id}:partition", json={"partition": "holdout"}
        )
    assert ok.status_code == 200 and again.status_code == 409
    audit.assert_called_once()
    test_db_session.refresh(enr)
    assert enr.partition == "development"
    assert (
        client.post("/v1/ops/enrollments/nope:partition", json={"partition": "holdout"}).status_code
        == 404
    )
    assert (
        client.post(
            f"/v1/ops/enrollments/{enr.enrollment_id}:partition", json={"partition": "test"}
        ).status_code
        == 422
    )


def test_session_create_schema_validates_client_metadata() -> None:
    import pytest as _pytest
    from pydantic import ValidationError

    from app.schemas.research import ParticipantSessionCreateRequest as R

    base = dict(participant_id="p", study_id="s", battery_id="b")
    ok = R(
        **base, administration_mode="native_macos", app_version="2.0.0+7", browser_family="Safari"
    )
    assert ok.administration_mode == "native_macos"
    for bad in (
        dict(administration_mode="tablet"),
        dict(app_version="1.0; drop"),
        dict(browser_family="<script>"),
    ):
        with _pytest.raises(ValidationError):
            R(**base, **bad)

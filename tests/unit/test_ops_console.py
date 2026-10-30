"""Unit tests for the /v1/ops read API and the /console static host."""

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
from app.database.models import AcquisitionUpload, Base
from app.main import create_app
from app.routes import ops
from app.services import ops_monitor
from app.services.authorization import get_current_user


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
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(user_id="admin-1")
    for guard in ops.GUARDS:
        app.dependency_overrides[guard] = lambda: None
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
    assert (
        client.get(
            "/v1/ops/catalog/session/nope", params={"purpose": "operations_support"}
        ).status_code
        == 404
    )


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
    detail = client.get(
        f"/v1/ops/catalog/session/{ps.participant_session_id}",
        params={"purpose": "operations_support"},
    ).json()["data"]
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


def _research_consent(participant_id: str, version: Any = None, revoked: bool = False) -> Any:
    from app.config import settings
    from app.database.models import Consent, ConsentType

    return Consent(
        participant_id=participant_id,
        consent_type=ConsentType.RESEARCH_PARTICIPATION,
        version=version or settings.consent_current_versions["research_participation"],
        consent_text_hash="h" * 64,
        revoked_at=datetime.now(timezone.utc) if revoked else None,
    )


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
    for person in people:
        db.add(_research_consent(person.participant_id))
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


# ---------------------------------------------------------------------------
# Governance: scopes, purpose-bound reads, audit, withdrawal
# ---------------------------------------------------------------------------


def _session_for(db: Any, origin: Any, consent: Any = "current", erased: bool = False) -> Any:
    """One session whose participant has the given consent state ('current'|'revoked'|'old'|None)."""
    from app.database.models import Battery, Participant, ParticipantSession, Study

    study = Study(name="S")
    db.add(study)
    db.commit()
    battery = Battery(study_id=study.study_id, name="B", version="1", instrument_schema={})
    person = Participant(is_deleted=erased)
    db.add_all([battery, person])
    db.commit()
    if consent == "current":
        db.add(_research_consent(person.participant_id))
    elif consent == "revoked":
        db.add(_research_consent(person.participant_id, revoked=True))
    elif consent == "old":
        db.add(_research_consent(person.participant_id, version="0.0.1-old"))
    ps = ParticipantSession(
        participant_id=person.participant_id,
        study_id=study.study_id,
        battery_id=battery.battery_id,
        data_origin=origin,
        status="completed",
    )
    db.add(ps)
    db.commit()
    return ps


@pytest.mark.parametrize(
    "origin,consent,erased,visible",
    [
        ("human_observed", "current", False, True),
        ("human_observed", "revoked", False, False),  # withdrawal
        ("human_observed", None, False, False),  # never consented
        ("human_observed", "old", False, False),  # consent text changed, not re-granted
        (None, None, False, False),  # unclassified is not assumed synthetic
        ("synthetic", None, False, True),  # no person, no consent needed
        ("synthetic", "revoked", True, False),  # erased always leaves
        ("human_observed", "current", True, False),
    ],
)
def test_research_views_respect_withdrawal(
    client: TestClient, test_db_session: Any, origin: Any, consent: Any, erased: bool, visible: bool
) -> None:
    _session_for(test_db_session, origin, consent, erased)
    data = client.get("/v1/ops/data-quality").json()["data"]
    assert data["session_count"] == (1 if visible else 0)
    assert data["withheld_sessions"] == (0 if visible else 1)
    home = client.get("/v1/ops/home").json()["data"]["quality"]["completion"]
    assert home["total"] == (1 if visible else 0)


def test_withdrawal_does_not_change_factual_origin(
    client: TestClient, test_db_session: Any
) -> None:
    ps = _session_for(test_db_session, "human_observed", "revoked")
    test_db_session.refresh(ps)
    assert ps.data_origin == "human_observed"


def test_erased_session_is_gone_from_catalog_and_detail(
    client: TestClient, test_db_session: Any
) -> None:
    ps = _session_for(test_db_session, "human_observed", "current", erased=True)
    assert client.get("/v1/ops/catalog", params={"kind": "session"}).json()["data"]["total"] == 0
    r = client.get(
        f"/v1/ops/catalog/session/{ps.participant_session_id}", params={"purpose": "research_qa"}
    )
    assert r.status_code == 404


def test_operational_views_keep_working_after_research_withdrawal(
    client: TestClient, test_db_session: Any
) -> None:
    """Declining research does not stop product processing: the session stays operable."""
    ps = _session_for(test_db_session, "human_observed", "revoked")
    assert client.get("/v1/ops/catalog", params={"kind": "session"}).json()["data"]["total"] == 1
    r = client.get(
        f"/v1/ops/catalog/session/{ps.participant_session_id}",
        params={"purpose": "operations_support"},
    )
    assert r.status_code == 200


def test_session_read_requires_purpose_and_is_audited(
    client: TestClient, test_db_session: Any
) -> None:
    from app.database.models import AuditLog

    ps = _session_for(test_db_session, "synthetic", None)
    url = f"/v1/ops/catalog/session/{ps.participant_session_id}"
    assert client.get(url).status_code == 422
    assert client.get(url, params={"purpose": "curiosity"}).status_code == 422
    assert test_db_session.query(AuditLog).count() == 0
    assert client.get(url, params={"purpose": "security_investigation"}).status_code == 200
    row = test_db_session.query(AuditLog).one()
    assert row.action == "ops:session_read" and row.resource_id == ps.participant_session_id
    assert row.user_id == "admin-1" and row.details["purpose"] == "security_investigation"


def test_catalog_and_quality_reads_are_audited_without_results(
    client: TestClient, test_db_session: Any
) -> None:
    from app.database.models import AuditLog

    client.get("/v1/ops/catalog", params={"q": "secret-term"})
    client.get("/v1/ops/data-quality")
    client.get("/v1/ops/failures")
    rows = {r.action: r for r in test_db_session.query(AuditLog)}
    assert {"ops:catalog_search", "ops:data_quality_read", "ops:failures_read"} <= set(rows)
    assert rows["ops:catalog_search"].details["q_given"] is True
    assert "secret-term" not in str(rows["ops:catalog_search"].details)


def test_read_is_refused_when_audit_cannot_be_written(
    client: TestClient, test_db_session: Any
) -> None:
    ps = _session_for(test_db_session, "synthetic", None)
    with patch.object(test_db_session, "commit", side_effect=RuntimeError("audit store down")):
        r = client.get(
            f"/v1/ops/catalog/session/{ps.participant_session_id}",
            params={"purpose": "research_qa"},
        )
    assert r.status_code == 500 and "events" not in r.text


# ---- access-denial matrix: every role x every /v1/ops route ----


def _routes() -> Any:
    out = []
    for route in ops.router.routes:
        for method in route.methods - {"HEAD", "OPTIONS"}:
            path = (
                route.path.replace("{job_id}", "x")
                .replace("{session_id}", "x")
                .replace("{alert_id}", "x")
                .replace("{enrollment_id}", "x")
            )
            out.append((method, path))
    return out


def _bare_client(roles: Any) -> Any:
    from app.services.authorization import get_current_user

    app = create_app(test_mode=True)
    if roles is not None:
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            user_id="u", username="u", roles=roles, permissions=None
        )
    return TestClient(app, raise_server_exceptions=False)


def test_route_scopes_are_pinned() -> None:
    """Adding a /v1/ops route must be a conscious choice of scope: update this table with it."""
    assert [d.dependency for d in ops.router.dependencies] == [ops.READ]
    guards = {}
    for route in ops.router.routes:
        extra = {d.call for d in route.dependant.dependencies} & {ops.WRITE, ops.QUALITY}
        guards[route.path] = extra
    write = {p for p, g in guards.items() if g == {ops.WRITE}}
    quality = {p for p, g in guards.items() if g == {ops.QUALITY}}
    assert write == {
        "/v1/ops/jobs/{job_id}:retry",
        "/v1/ops/sessions/{session_id}:classify",
        "/v1/ops/alerts/{alert_id}:ack",
        "/v1/ops/enrollments/{enrollment_id}:partition",
    }
    assert quality == {"/v1/ops/data-quality", "/v1/ops/studies"}
    assert len(guards) == 13


@pytest.mark.parametrize("roles", [["researcher"], ["viewer"], [], ["unknown-role"]])
def test_non_admin_roles_are_denied_on_every_ops_route(roles: Any) -> None:
    with _bare_client(roles) as c:
        for method, path in _routes():
            r = c.request(method, path, params={"purpose": "research_qa"}, json={})
            assert r.status_code in (401, 403), (roles, method, path, r.status_code)


def test_unauthenticated_requests_are_denied_on_every_ops_route() -> None:
    with _bare_client(None) as c:
        for method, path in _routes():
            r = c.request(method, path, params={"purpose": "research_qa"}, json={})
            assert r.status_code in (401, 403), (method, path, r.status_code)


def test_api_key_with_system_admin_alone_no_longer_opens_the_console() -> None:
    from app.services.authorization import get_current_user

    app = create_app(test_mode=True)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        user_id="k", username="k", roles=[], permissions=["system:admin"]
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        assert c.get("/v1/ops/home").status_code in (401, 403)


def test_scopes_are_separate_read_cannot_write_or_see_quality() -> None:
    from app.services.authorization import get_current_user

    app = create_app(test_mode=True)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        user_id="k", username="k", roles=[], permissions=["ops:read"]
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        assert c.post("/v1/ops/jobs/job:x:retry").status_code in (401, 403)
        assert c.post("/v1/ops/alerts/x:ack").status_code in (401, 403)
        assert c.get("/v1/ops/data-quality").status_code in (401, 403)
        assert c.get("/v1/ops/studies").status_code in (401, 403)


def test_console_reads_join_the_signed_hash_chain(client: TestClient, test_db_session: Any) -> None:
    """Console audit entries must chain like every other entry, or verify_chain() would flag them."""
    from app.config import settings
    from app.database.models import AuditLog
    from app.services.audit_signing import verify_chain

    ps = _session_for(test_db_session, "synthetic", None)
    with patch.object(settings, "audit_signing_key", "k" * 40):
        client.get("/v1/ops/failures")
        client.get(
            f"/v1/ops/catalog/session/{ps.participant_session_id}",
            params={"purpose": "research_qa"},
        )
        rows = test_db_session.query(AuditLog).order_by(AuditLog.chain_seq).all()
        assert [r.chain_seq for r in rows] == [1, 2]
        assert verify_chain(rows) == (True, None)

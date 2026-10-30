"""Fault-injection tests for operational alerting: routing, dedup, ack, recovery."""

from datetime import datetime, timedelta, timezone
from typing import Any, Generator, List, Tuple
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database.connection import get_db
from app.database.models import AcquisitionUpload, Base, OpsAlert, Task, TaskStatus
from app.main import create_app
from app.routes import ops
from app.services import job_tracker, ops_alerts


@pytest.fixture
def db() -> Generator[Any, None, None]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()
    engine.dispose()


@pytest.fixture
def sent() -> Generator[List[Tuple[str, str]], None, None]:
    log: List[Tuple[str, str]] = []

    def fake(title: str, message: str, severity: str, meta: Any) -> bool:
        log.append((title, severity))
        return True

    with patch("app.services.ops_alerts.notify", fake):
        yield log


def _ago(**kw: Any) -> datetime:
    return datetime.now(timezone.utc) - timedelta(**kw)


def _dead_job(db: Any) -> Any:
    j = job_tracker.record_enqueue(db, "scoring", "s1")
    j.status, j.error = "dead", "boom"
    db.commit()
    return j


def test_dead_job_alerts_once_then_dedups(db: Any, sent: Any) -> None:
    _dead_job(db)
    assert ops_alerts.run_cycle(db)["opened"] == 1
    assert ops_alerts.run_cycle(db) == {"opened": 0, "resolved": 0, "reminded": 0, "unchanged": 1}
    assert len(sent) == 1 and sent[0][1] == "error"
    assert db.query(OpsAlert).count() == 1


def test_recovery_notice_when_condition_clears(db: Any, sent: Any) -> None:
    j = _dead_job(db)
    ops_alerts.run_cycle(db)
    j.status = "succeeded"
    db.commit()
    assert ops_alerts.run_cycle(db)["resolved"] == 1
    assert sent[-1][0].startswith("Recovered:") and sent[-1][1] == "info"
    assert db.query(OpsAlert).one().status == "resolved"


def test_acknowledged_alert_is_not_reminded_but_still_recovers(db: Any, sent: Any) -> None:
    j = _dead_job(db)
    ops_alerts.run_cycle(db)
    alert = db.query(OpsAlert).one()
    ops_alerts.acknowledge(db, alert.alert_id, "op1")
    later = datetime.now(timezone.utc) + timedelta(days=3)
    assert ops_alerts.run_cycle(db, now=later)["reminded"] == 0
    assert len(sent) == 1
    j.status = "succeeded"
    db.commit()
    ops_alerts.run_cycle(db)
    db.refresh(alert)
    assert alert.status == "resolved" and alert.acknowledged_by == "op1"


def test_unacknowledged_alert_reminds_after_window(db: Any, sent: Any) -> None:
    _dead_job(db)
    ops_alerts.run_cycle(db)
    later = datetime.now(timezone.utc) + timedelta(hours=25)
    assert ops_alerts.run_cycle(db, now=later)["reminded"] == 1
    assert sent[-1][0].startswith("Reminder:")


def test_stale_source_and_thresholds(db: Any, sent: Any) -> None:
    old = _ago(days=3)
    db.add(
        Task(
            session_id="sess",
            task_type="x",
            parameters={},
            status=TaskStatus.PENDING,
            created_at=old,
        )
    )
    for i in range(5):
        db.add(
            AcquisitionUpload(
                session_id="s",
                object_name=f"o{i}",
                size_bytes=1,
                crc32c=1,
                sha256="a" * 64,
                sequence=i,
                principal="p",
                status="quarantined",
                reason="x",
                committed=True,
            )
        )
    for _ in range(5):
        db.add(
            Task(
                session_id="sess",
                task_type="x",
                parameters={},
                status=TaskStatus.FAILED,
                created_at=old,
                completed_at=_ago(minutes=5),
            )
        )
    db.commit()
    ops_alerts.run_cycle(db)
    rules = {a.rule for a in db.query(OpsAlert)}
    assert {"stale_source", "quarantine_rate", "task_failures"} <= rules


def test_no_data_is_unknown_not_an_alert(db: Any, sent: Any) -> None:
    assert ops_alerts.run_cycle(db)["opened"] == 0 and sent == []


def test_undelivered_alert_is_retried_next_cycle(db: Any) -> None:
    _dead_job(db)
    with patch("app.services.ops_alerts.notify", return_value=False):
        ops_alerts.run_cycle(db)
    assert db.query(OpsAlert).one().notify_count == 0
    with patch("app.services.ops_alerts.notify", return_value=True) as n:
        ops_alerts.run_cycle(db)
    n.assert_called_once()
    assert db.query(OpsAlert).one().notify_count == 1


def test_notify_without_channels_reports_failure() -> None:
    with patch("app.services.ops_alerts.build_channels", return_value=[]):
        assert ops_alerts.notify("t", "m", "error", {}) is False


def test_notify_routes_to_channel() -> None:
    class Chan:
        got: List[Any] = []

        async def send_alert(self, alert: Any) -> bool:
            Chan.got.append(alert.title)
            return True

    with patch("app.services.ops_alerts.build_channels", return_value=[Chan()]):
        assert ops_alerts.notify("hello", "m", "warning", {}) is True
    assert Chan.got == ["hello"]


def test_alerts_api_list_and_ack(db: Any, sent: Any) -> None:
    from types import SimpleNamespace

    from app.services.authorization import get_current_user

    _dead_job(db)
    ops_alerts.run_cycle(db)
    app = create_app(test_mode=True)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(user_id="op1")
    for dep in ops.router.dependencies:
        app.dependency_overrides[dep.dependency] = lambda: None
    with TestClient(app, raise_server_exceptions=False) as c:
        rows = c.get("/v1/ops/alerts").json()["data"]
        assert rows[0]["status"] == "open" and rows[0]["notified"] is True
        assert c.get("/v1/ops/home").json()["data"]["alerts_open"] == 1
        with patch("app.routes.ops.log_audit_event"):
            r = c.post(f"/v1/ops/alerts/{rows[0]['alert_id']}:ack")
        assert r.status_code == 200 and r.json()["data"]["status"] == "acknowledged"
        assert c.post("/v1/ops/alerts/nope:ack").status_code == 404

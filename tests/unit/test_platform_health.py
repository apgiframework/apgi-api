"""Platform-health readings: Cloud Monitoring parsing, backups, failure isolation, alerts."""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict
from unittest.mock import patch

import pytest

from app.config import settings
from app.services import ops_alerts, platform_health

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)


def _series(value: float, **labels: str) -> Dict[str, Any]:
    return {"metric": {"labels": labels}, "points": [{"value": {"doubleValue": value}}]}


def fake_fetch(url: str, params: Dict[str, Any]) -> Dict[str, Any]:
    if "backupRuns" in url:
        return {
            "items": [
                {"status": "FAILED", "endTime": "2026-10-30T03:00:00Z"},
                {"status": "SUCCESSFUL", "endTime": "2026-10-29T03:00:00Z"},
                {"status": "SUCCESSFUL", "endTime": "2026-10-28T03:00:00Z"},
            ]
        }
    if "request_count" in params["filter"]:
        return {
            "timeSeries": [
                _series(950, response_code_class="2xx"),
                _series(10, response_code_class="4xx"),
                _series(40, response_code_class="5xx"),
            ]
        }
    return {"timeSeries": [_series(120.0), _series(310.5)]}


def test_request_metrics_parse_ratio_and_worst_p95() -> None:
    m = platform_health.read_request_metrics(fake_fetch, "proj", "svc", NOW)
    assert m["request_count_1h"] == 1000
    assert m["error_ratio_5xx"] == 0.04 and m["success_ratio"] == 0.96
    assert m["p95_latency_ms"] == 310.5


def test_no_traffic_is_unknown_not_100_percent() -> None:
    empty = lambda url, params: {"timeSeries": []}  # noqa: E731
    m = platform_health.read_request_metrics(empty, "p", "s", NOW)
    assert (
        m["success_ratio"] is None and m["error_ratio_5xx"] is None and m["p95_latency_ms"] is None
    )


def test_last_backup_ignores_failed_runs() -> None:
    assert platform_health.read_last_backup(fake_fetch, "p", "i") == "2026-10-29T03:00:00Z"


def _snap(**cfg: Any) -> Dict[str, Any]:
    with (
        patch.multiple(
            settings, gcp_project_id="proj", cloud_run_service="svc", cloud_sql_instance="db", **cfg
        ),
        patch.object(platform_health, "probe_database", return_value=2.5),
        patch.object(platform_health, "probe_workers", return_value=2),
    ):
        return platform_health.build_snapshot(lambda: fake_fetch, NOW)


def test_snapshot_combines_sources() -> None:
    s = _snap()
    assert s["success_ratio_1h"] == 0.96 and s["last_backup_at"] == "2026-10-29T03:00:00Z"
    assert s["database_latency_ms"] == 2.5 and s["celery_workers_online"] == 2
    assert s["errors"] == [] and s["as_of"]["cloud_monitoring"] is not None


def test_unconfigured_cloud_sources_are_unknown_with_explanation() -> None:
    with (
        patch.multiple(
            settings, gcp_project_id=None, cloud_run_service=None, cloud_sql_instance=None
        ),
        patch.object(platform_health, "probe_database", return_value=1.0),
        patch.object(platform_health, "probe_workers", return_value=0),
    ):
        s = platform_health.build_snapshot(lambda: fake_fetch, NOW)
    assert s["success_ratio_1h"] is None and s["last_backup_at"] is None
    assert "CLOUD_RUN_SERVICE" in s["source"] and s["celery_workers_online"] == 0


def test_one_failing_source_does_not_hide_the_others_and_leaks_no_message() -> None:
    def broken(url: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if "backupRuns" in url:
            raise RuntimeError("https://secret-token-url")
        return fake_fetch(url, params)

    with (
        patch.multiple(settings, gcp_project_id="p", cloud_run_service="s", cloud_sql_instance="i"),
        patch.object(platform_health, "probe_database", side_effect=OSError("db down")),
        patch.object(platform_health, "probe_workers", return_value=1),
    ):
        s = platform_health.build_snapshot(lambda: broken, NOW)
    assert s["success_ratio_1h"] == 0.96 and s["last_backup_at"] is None
    assert s["database_latency_ms"] is None
    assert sorted(s["errors"]) == ["cloud_sql_backups: RuntimeError", "database: OSError"]
    assert "secret" not in str(s)


def test_hung_probe_is_cut_off() -> None:
    import time

    errors: list = []
    assert (
        platform_health._attempt("slow", errors, lambda: time.sleep(2), hard_timeout=0.05) is None
    )
    assert errors and errors[0].startswith("slow:")


def test_snapshot_is_cached() -> None:
    platform_health.reset_cache()
    with patch.object(platform_health, "build_snapshot", return_value={"x": 1}) as b:
        platform_health.snapshot()
        platform_health.snapshot()
        platform_health.snapshot(force=True)
    assert b.call_count == 2
    platform_health.reset_cache()


@pytest.mark.parametrize(
    "snap,expected",
    [
        ({"error_ratio_5xx": 0.10, "request_count_1h": 100}, {"api_5xx"}),
        ({"error_ratio_5xx": 0.10, "request_count_1h": 5}, set()),  # too little traffic
        ({"error_ratio_5xx": None, "request_count_1h": None}, set()),
        ({"last_backup_at": (NOW - timedelta(hours=60)).isoformat()}, {"backup_stale"}),
        ({"last_backup_at": (NOW - timedelta(hours=2)).isoformat()}, set()),
        ({"last_backup_at": None}, set()),
    ],
)
def test_platform_alert_rules(snap: Dict[str, Any], expected: set) -> None:
    with patch("app.services.platform_health.snapshot", lambda force=False: snap):
        assert {f.rule for f in ops_alerts._platform_findings(NOW)} == expected

"""End-to-end screening: quality submission, norm exclusion, duplicate
flagging (flag, not block), one-norm-session rule, form counterbalancing and
the dataset-card exclusion report."""

from typing import Any

from sqlalchemy.orm import sessionmaker
from starlette.testclient import TestClient

from app.database.models import ParticipantSession, StudyEnrollment
from tests.integration.test_reporting_integration import _client, db_engine  # noqa: F401

GOOD = {
    "tier_config_version": "tier_config-1.0.0",
    "timing_errors_ms": [1.0, 2.0, 3.0],
    "fullscreen": True,
    "attention_checks_passed": 2,
}


def _setup(db_engine: Any, forms: tuple[str, ...] = ("A",)) -> tuple[TestClient, str, list[str]]:
    r = _client(db_engine, ["researcher"], "researcher-1")
    study_id = r.post("/v1/studies", json={"name": "S"}).json()["study_id"]
    batteries = [
        r.post(
            "/v1/batteries",
            json={"study_id": study_id, "name": "B", "version": "1.0", "form_label": f},
        ).json()["battery_id"]
        for f in forms
    ]
    return r, study_id, batteries


def _session(
    db_engine: Any, user: str, study_id: str, battery_id: str, fp: str | None = None
) -> tuple[TestClient, str, str, dict[str, Any]]:
    c = _client(db_engine, ["viewer"], user)
    pid = c.post("/v1/participants", json={}).json()["participant_id"]
    c.post(
        f"/v1/participants/{pid}/consents",
        json={"consent_type": "research_participation"},
    )
    body: dict[str, Any] = {"participant_id": pid, "study_id": study_id, "battery_id": battery_id}
    if fp:
        body["device_fingerprint"] = fp
    resp = c.post("/v1/participant-sessions", json=body)
    assert resp.status_code == 201, resp.text
    return c, pid, resp.json()["participant_session_id"], resp.json()


def _flags(db_engine: Any, psid: str) -> ParticipantSession:
    db = sessionmaker(bind=db_engine)()
    row = db.query(ParticipantSession).filter_by(participant_session_id=psid).one()
    db.expunge(row)
    db.close()
    return row


def test_good_session_is_norm_eligible(db_engine: Any) -> None:
    _, study_id, (bat,) = _setup(db_engine)
    c, _, psid, _ = _session(db_engine, "u1", study_id, bat)
    resp = c.post(f"/v1/participant-sessions/{psid}/quality", json=GOOD)
    assert resp.status_code == 200
    assert resp.json()["device_tier"] == "A"
    assert resp.json()["tier_config_version"] == "tier_config-1.0.0"
    assert resp.json()["norm_eligible"] is True
    row = _flags(db_engine, psid)
    assert row.timing_error_distribution["p95"] == 3.0  # distribution kept, not just mean


def test_two_failed_attention_checks_exclude_from_norms(db_engine: Any) -> None:
    _, study_id, (bat,) = _setup(db_engine)
    c, _, psid, _ = _session(db_engine, "u1", study_id, bat)
    resp = c.post(
        f"/v1/participant-sessions/{psid}/quality", json={**GOOD, "attention_checks_failed": 2}
    )
    assert resp.json()["norm_eligible"] is False
    assert resp.json()["device_tier"] == "C"
    assert "failed_attention_checks" in _flags(db_engine, psid).screening["exclusion_reasons"]


def test_unknown_tier_config_version_rejected(db_engine: Any) -> None:
    _, study_id, (bat,) = _setup(db_engine)
    c, _, psid, _ = _session(db_engine, "u1", study_id, bat)
    resp = c.post(
        f"/v1/participant-sessions/{psid}/quality", json={**GOOD, "tier_config_version": "old"}
    )
    assert resp.status_code == 409


def test_bot_and_questionnaire_flags_recorded(db_engine: Any) -> None:
    _, study_id, (bat,) = _setup(db_engine)
    c, _, psid, _ = _session(db_engine, "u1", study_id, bat)
    c.post(
        f"/v1/participant-sessions/{psid}/quality",
        json={**GOOD, "honeypot": "spam", "questionnaire_responses": [3] * 10},
    )
    screening = _flags(db_engine, psid).screening
    assert "bot:honeypot_filled" in screening["flags"]
    assert "straight_lining" in screening["flags"]
    assert _flags(db_engine, psid).norm_eligible is False


def test_duplicate_fingerprint_is_flagged_not_blocked(db_engine: Any) -> None:
    _, study_id, (bat,) = _setup(db_engine)
    c1, _, s1, _ = _session(db_engine, "u1", study_id, bat, fp="fp-1")
    c2, _, s2, _ = _session(db_engine, "u2", study_id, bat, fp="fp-1")
    c1.post(f"/v1/participant-sessions/{s1}/quality", json=GOOD)
    resp = c2.post(f"/v1/participant-sessions/{s2}/quality", json=GOOD)
    assert resp.status_code == 200
    assert "duplicate_device_fingerprint" in _flags(db_engine, s2).screening["flags"]
    assert resp.json()["norm_eligible"] is True  # flag, not block


def test_one_norm_session_per_participant_per_battery_version(db_engine: Any) -> None:
    _, study_id, (bat,) = _setup(db_engine)
    c, pid, s1, _ = _session(db_engine, "u1", study_id, bat)
    c.post(f"/v1/participant-sessions/{s1}/quality", json=GOOD)
    s2 = c.post(
        "/v1/participant-sessions",
        json={"participant_id": pid, "study_id": study_id, "battery_id": bat},
    ).json()["participant_session_id"]
    resp = c.post(f"/v1/participant-sessions/{s2}/quality", json=GOOD)
    assert resp.json()["norm_eligible"] is False
    assert (
        "repeat_session_for_battery_version" in _flags(db_engine, s2).screening["exclusion_reasons"]
    )


def test_unscreened_completion_is_kept_out_of_norms(db_engine: Any) -> None:
    _, study_id, (bat,) = _setup(db_engine)
    c, _, psid, _ = _session(db_engine, "u1", study_id, bat)
    assert c.post(f"/v1/participant-sessions/{psid}/complete").status_code == 200
    row = _flags(db_engine, psid)
    assert row.norm_eligible is False
    assert row.screening["exclusion_reasons"] == ["no_device_telemetry"]


def test_form_order_fixed_at_enrolment_and_balanced(db_engine: Any) -> None:
    _, study_id, bats = _setup(db_engine, forms=("A", "B", "C"))
    orders = []
    for i in range(3):
        c, pid, psid, body = _session(db_engine, f"u{i}", study_id, bats[0])
        orders.append(body["expected_form_label"])
        # a later session must not change the stored order
        c.post(
            "/v1/participant-sessions",
            json={"participant_id": pid, "study_id": study_id, "battery_id": bats[0]},
        )
    db = sessionmaker(bind=db_engine)()
    stored = [e.form_order for e in db.query(StudyEnrollment).order_by(StudyEnrollment.enrolled_at)]
    db.close()
    assert sorted(o[0] for o in stored) == ["A", "B", "C"]  # first-position balance
    assert all(len(o) == 3 for o in stored)
    assert sorted(orders) == ["A", "B", "C"]


def test_exclusion_report(db_engine: Any) -> None:
    r, study_id, (bat,) = _setup(db_engine)
    c1, _, s1, _ = _session(db_engine, "u1", study_id, bat)
    c2, _, s2, _ = _session(db_engine, "u2", study_id, bat)
    c1.post(f"/v1/participant-sessions/{s1}/quality", json=GOOD)
    c2.post(f"/v1/participant-sessions/{s2}/quality", json={**GOOD, "is_mobile": True})
    for c, s in ((c1, s1), (c2, s2)):
        c.post(f"/v1/participant-sessions/{s}/complete")
    rep = r.get(f"/v1/studies/{study_id}/exclusion-report").json()
    assert rep["n_screened_sessions"] == 2
    assert rep["n_excluded_from_norms"] == 1
    assert rep["exclusion_rate"] == 0.5
    assert rep["by_reason"] == {"device_tier_c": 1}
    assert rep["by_device_tier"] == {"A": 1, "B": 0, "C": 1}
    viewer = _client(db_engine, ["viewer"], "v")
    assert viewer.get(f"/v1/studies/{study_id}/exclusion-report").status_code == 403

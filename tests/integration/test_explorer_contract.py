"""Explorer research-client contract: config, pinned bank, canonical consent,
idempotent journal upload with server_verified acks, allow-listed scores."""

import uuid
from typing import Any

from starlette.testclient import TestClient

from app.services import explorer_contract as contract
from tests.integration.test_reporting_integration import _client, db_engine  # noqa: F401

PURPOSES = ("product_use", "research_participation")


def _event(seq: int, item: int, value: int, previous: int | None = None) -> dict[str, Any]:
    payload = {
        "item_id": item,
        "value": value,
        "latency_us": 1500000 + seq,
        "instrument_sha256": contract.item_bank()["sha256"],
    }
    if previous is not None:
        payload["previous_value"] = previous
    return {
        "event_id": str(uuid.uuid4()),
        "seq_no": seq,
        "payload": payload,
        "payload_sha256": contract.sha256_hex(contract.canonical_json(payload)),
    }


def _session(db_engine: Any, consents: tuple[str, ...] = PURPOSES) -> tuple[TestClient, str]:
    r = _client(db_engine, ["researcher"], "researcher-1")
    study = r.post("/v1/studies", json={"name": "S"}).json()["study_id"]
    battery = r.post(
        "/v1/batteries", json={"study_id": study, "name": "B", "version": "1.0", "form_label": "A"}
    ).json()["battery_id"]
    c = _client(db_engine, ["viewer"], "u1")
    pid = c.post("/v1/participants", json={}).json()["participant_id"]
    for purpose in ("research_participation", *[p for p in consents if p != "research_participation"]):
        if purpose in consents:
            resp = c.post(f"/v1/participants/{pid}/consents", json={"consent_type": purpose})
            assert resp.status_code == 201, resp.text
    resp = c.post(
        "/v1/participant-sessions",
        json={"participant_id": pid, "study_id": study, "battery_id": battery},
    )
    assert resp.status_code == 201, resp.text
    return c, resp.json()["participant_session_id"]


def test_config_ships_research_off_with_draft_consents(db_engine: Any) -> None:
    body = _client(db_engine, [], "anon").get("/v1/explorer/config").json()
    assert body["research_enabled"] is False
    assert body["paid_products_enabled"] is False
    assert {p["consent_type"] for p in body["consent_purposes"]} == {
        "product_use",
        "research_participation",
        "data_sharing",
        "recontact",
    }
    assert {p["status"] for p in body["consent_purposes"]} == {"draft"}
    assert body["instrument"]["item_count"] == 35


def test_bank_is_pinned_and_hash_is_reproducible(db_engine: Any) -> None:
    bank = _client(db_engine, [], "anon").get("/v1/instruments/apgi-explorer-35").json()
    assert [i["item_id"] for i in bank["items"]] == list(range(1, 36))
    assert "note" not in bank["items"][0]["options"][0]
    unsigned = {k: v for k, v in bank.items() if k != "sha256"}
    assert contract.sha256_hex(contract.canonical_json(unsigned)) == bank["sha256"]


def test_canonical_consent_text_is_served_and_recorded(db_engine: Any) -> None:
    c = _client(db_engine, ["viewer"], "u1")
    assert len(c.get("/v1/consent-texts").json()) == 4
    pid = c.post("/v1/participants", json={}).json()["participant_id"]
    resp = c.post(f"/v1/participants/{pid}/consents", json={"consent_type": "recontact"})
    assert resp.status_code == 201
    status = c.get(f"/v1/participants/{pid}/consents/status").json()
    assert status["recontact"] is True and status["product_use"] is False


def test_upload_is_idempotent_and_acknowledged(db_engine: Any) -> None:
    c, psid = _session(db_engine)
    batch = {"events": [_event(0, 1, 2), _event(1, 2, 3)]}
    first = c.post(f"/v1/participant-sessions/{psid}/events", json=batch).json()["acks"]
    assert [a["status"] for a in first] == ["accepted", "accepted"]
    assert all(a["server_verified"] for a in first)
    again = c.post(f"/v1/participant-sessions/{psid}/events", json=batch).json()["acks"]
    assert [a["status"] for a in again] == ["duplicate", "duplicate"]


def test_conflicting_reuse_of_seq_no_is_409(db_engine: Any) -> None:
    c, psid = _session(db_engine)
    c.post(f"/v1/participant-sessions/{psid}/events", json={"events": [_event(0, 1, 2)]})
    resp = c.post(f"/v1/participant-sessions/{psid}/events", json={"events": [_event(0, 1, 4)]})
    assert resp.status_code == 409


def test_bad_hash_unknown_item_and_out_of_range_rejected(db_engine: Any) -> None:
    c, psid = _session(db_engine)
    tampered = _event(0, 1, 2)
    tampered["payload_sha256"] = "0" * 64
    for event in (tampered, _event(1, 99, 2), _event(2, 1, 5)):
        resp = c.post(f"/v1/participant-sessions/{psid}/events", json={"events": [event]})
        assert resp.status_code == 422, resp.text


def test_upload_requires_product_and_research_consent(db_engine: Any) -> None:
    c, psid = _session(db_engine, consents=("research_participation",))
    resp = c.post(f"/v1/participant-sessions/{psid}/events", json={"events": [_event(0, 1, 2)]})
    assert resp.status_code == 403


def test_result_exposes_only_allowlisted_fields(db_engine: Any) -> None:
    c, psid = _session(db_engine)
    events = [_event(i, i + 1, 1 + (i % 4)) for i in range(35)]
    assert c.get(f"/v1/participant-sessions/{psid}/explorer-result").status_code == 409
    c.post(f"/v1/participant-sessions/{psid}/events", json={"events": events})
    body = c.get(f"/v1/participant-sessions/{psid}/explorer-result").json()
    allowed = {e["field"] for e in contract.SCORE_ALLOWLIST.values()}
    assert set(body["scores"]) == allowed
    assert body["excluded_from_norms"] is True and body["analysis_mode"] == "exploratory"


def test_scoring_subscales_independent_and_nominal_items_excluded() -> None:
    base = {i: 2 for i in range(1, 36)}
    a, b = contract.score_answers(base), contract.score_answers({**base, 1: 4})
    assert a["epsilon"] != b["epsilon"]
    assert all(a[k] == b[k] for k in a if k != "epsilon")
    assert contract.score_answers({**base, 7: 4})["precision"] == a["precision"]

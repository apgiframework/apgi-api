"""Versioned instrument lookup and /responses (S1-EP-06.4/6.5, S3-INS-01)."""

from typing import Any

from sqlalchemy.orm import Session

from app.database.models import ParticipantSession
from app.services import explorer_contract as contract
from tests.integration.test_explorer_contract import _session
from tests.integration.test_reporting_integration import _client, db_engine  # noqa: F401


def _batch(sha: str, *answers: tuple[str, int, int], version: str = "1.0.0") -> dict[str, Any]:
    return {
        "instrument_id": contract.INSTRUMENT_ID,
        "instrument_version": version,
        "instrument_sha256": sha,
        "responses": [{"event_id": e, "item_id": i, "value": v} for e, i, v in answers],
    }


def test_version_addressed_lookup_is_public_and_matches_pinned_bank(db_engine: Any) -> None:
    anon = _client(db_engine, [], "anon")
    resp = anon.get("/v1/instruments/apgi-explorer-35/versions/1.0.0")
    assert resp.status_code == 200
    assert resp.json()["sha256"] == contract.item_bank()["sha256"]
    assert anon.get("/v1/instruments/apgi-explorer-35/versions/9.9.9").status_code == 404
    assert anon.get("/v1/instruments/nope/versions/1.0.0").status_code == 404


def test_responses_are_idempotent_and_conflicts_are_409(db_engine: Any) -> None:
    c, sid = _session(db_engine)
    sha = contract.item_bank()["sha256"]
    url = f"/v1/participant-sessions/{sid}/responses"

    first = c.post(url, json=_batch(sha, ("r1", 1, 3), ("r2", 2, 4)))
    assert first.status_code == 201, first.text
    assert [a["status"] for a in first.json()["acks"]] == ["accepted", "accepted"]

    again = c.post(url, json=_batch(sha, ("r1", 1, 3)))
    assert again.json()["acks"][0]["status"] == "duplicate"

    assert c.post(url, json=_batch(sha, ("r1", 1, 2))).status_code == 409  # same id, new content
    assert c.post(url, json=_batch(sha, ("r9", 1, 2))).status_code == 409  # second answer to item 1


def test_responses_reject_wrong_hash_unknown_version_and_bad_values(db_engine: Any) -> None:
    c, sid = _session(db_engine)
    sha = contract.item_bank()["sha256"]
    url = f"/v1/participant-sessions/{sid}/responses"
    assert c.post(url, json=_batch("0" * 64, ("a", 1, 1))).status_code == 409
    assert c.post(url, json=_batch(sha, ("a", 1, 1), version="2.0.0")).status_code == 404
    assert c.post(url, json=_batch(sha, ("a", 1, 9))).status_code == 422
    assert c.post(url, json=_batch(sha, ("a", 99, 1))).status_code == 422


def test_responses_need_consent(db_engine: Any) -> None:
    c, sid = _session(db_engine, consents=("product_use",))
    with Session(db_engine) as db:
        pid = db.query(ParticipantSession).one().participant_id
    c.post(f"/v1/participants/{pid}/consents/revoke", json={"consent_type": "product_use"})
    resp = c.post(
        f"/v1/participant-sessions/{sid}/responses",
        json=_batch(contract.item_bank()["sha256"], ("a", 1, 1)),
    )
    assert resp.status_code == 403

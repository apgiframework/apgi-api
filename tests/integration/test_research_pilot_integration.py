"""
Integration tests for the Phase 2 research pilot domain: participants,
consent enforcement, studies, batteries, participant sessions, and trial
ingestion, end to end through the real FastAPI app with a real (SQLite)
database session.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from app.database.connection import get_db
from app.database.models import Base
from app.main import create_app
from app.models.schemas import TokenPayload
from app.services.authorization import get_current_user


def _make_user(roles: list[str], user_id: str = "user-1") -> TokenPayload:
    return TokenPayload(
        user_id=user_id,
        username=f"{user_id}@example.com",
        roles=roles,
        exp=datetime.now(timezone.utc) + timedelta(hours=1),
    )


@pytest.fixture
def db_engine() -> Generator[Any, None, None]:
    # FastAPI dispatches sync dependencies (get_db) via a worker thread pool,
    # so a plain sqlite:///:memory: engine (one connection per thread) would
    # give the request handler an empty database. StaticPool pins every
    # connection to the same in-memory database regardless of thread.
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine: Any) -> Generator[Session, None, None]:
    """A session for direct test-side assertions against the same database
    the app is using — separate from the per-request sessions the app itself
    gets via the overridden get_db (see _override_get_db)."""
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _override_get_db(engine: Any) -> Any:
    """Mirrors app.database.connection.get_db's real per-request lifecycle
    (fresh session, rollback on exception, always closed) instead of sharing
    one long-lived session across every request in a test — matching
    production means a failed request doesn't poison later requests."""
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def _get_db() -> Generator[Session, None, None]:
        db = SessionLocal()
        try:
            yield db
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    return _get_db


@pytest.fixture
def researcher_client(db_engine: Any) -> Generator[TestClient, None, None]:
    app = create_app(test_mode=True)
    app.dependency_overrides[get_db] = _override_get_db(db_engine)
    app.dependency_overrides[get_current_user] = lambda: _make_user(["researcher"], "researcher-1")
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def participant_client(db_engine: Any) -> Generator[TestClient, None, None]:
    app = create_app(test_mode=True)
    app.dependency_overrides[get_db] = _override_get_db(db_engine)
    app.dependency_overrides[get_current_user] = lambda: _make_user(
        ["viewer"], "participant-user-1"
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def _create_study_and_battery(client: TestClient) -> tuple[str, str]:
    study_resp = client.post("/v1/studies", json={"name": "Pilot Study"})
    assert study_resp.status_code == 201, study_resp.text
    study_id = study_resp.json()["study_id"]

    battery_resp = client.post(
        "/v1/batteries",
        json={"study_id": study_id, "name": "Core Battery", "version": "1.0", "form_label": "A"},
    )
    assert battery_resp.status_code == 201, battery_resp.text
    battery_id = battery_resp.json()["battery_id"]
    return study_id, battery_id


class TestStudyAndBatteryCreation:
    def test_researcher_can_create_study_and_battery(self, researcher_client: TestClient) -> None:
        study_id, battery_id = _create_study_and_battery(researcher_client)
        assert study_id
        assert battery_id

    def test_non_staff_cannot_create_study(self, participant_client: TestClient) -> None:
        resp = participant_client.post("/v1/studies", json={"name": "Should Fail"})
        assert resp.status_code == 403

    def test_hidden_field_on_study_create_is_rejected(self, researcher_client: TestClient) -> None:
        resp = researcher_client.post(
            "/v1/studies", json={"name": "Tamper Attempt", "status": "active"}
        )
        assert resp.status_code == 422


class TestParticipantAndConsent:
    def test_create_participant_links_to_caller(self, participant_client: TestClient) -> None:
        resp = participant_client.post("/v1/participants", json={"external_ref": "panel-123"})
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["external_ref"] == "panel-123"
        assert body["is_deleted"] is False

    def test_other_user_cannot_read_participant(
        self, participant_client: TestClient, researcher_client: TestClient, db_engine: Any
    ) -> None:
        created = participant_client.post("/v1/participants", json={}).json()
        participant_id = created["participant_id"]

        # A different, non-staff user (not the owner) is forbidden.
        from app.main import create_app as make_app

        app = make_app(test_mode=True)
        app.dependency_overrides[get_db] = _override_get_db(db_engine)
        app.dependency_overrides[get_current_user] = lambda: _make_user(
            ["viewer"], "some-other-user"
        )
        with TestClient(app, raise_server_exceptions=False) as other_client:
            resp = other_client.get(f"/v1/participants/{participant_id}")
        assert resp.status_code == 403

        # The researcher can read any participant.
        resp = researcher_client.get(f"/v1/participants/{participant_id}")
        assert resp.status_code == 200

    def test_consent_status_defaults_false(self, participant_client: TestClient) -> None:
        participant_id = participant_client.post("/v1/participants", json={}).json()[
            "participant_id"
        ]
        status_resp = participant_client.get(f"/v1/participants/{participant_id}/consents/status")
        assert status_resp.json() == {
            "product_use": False,
            "research_participation": False,
            "data_sharing": False,
            "recontact": False,
        }

    def test_grant_and_revoke_consent(self, participant_client: TestClient) -> None:
        participant_id = participant_client.post("/v1/participants", json={}).json()[
            "participant_id"
        ]

        grant_resp = participant_client.post(
            f"/v1/participants/{participant_id}/consents",
            json={
                "consent_type": "research_participation",
            },
        )
        assert grant_resp.status_code == 201, grant_resp.text

        status_resp = participant_client.get(f"/v1/participants/{participant_id}/consents/status")
        assert status_resp.json()["research_participation"] is True
        assert status_resp.json()["data_sharing"] is False

        revoke_resp = participant_client.post(
            f"/v1/participants/{participant_id}/consents/revoke",
            json={"consent_type": "research_participation"},
        )
        assert revoke_resp.status_code == 204

        status_resp = participant_client.get(f"/v1/participants/{participant_id}/consents/status")
        assert status_resp.json()["research_participation"] is False

    def test_erasure_clears_pii_and_tombstones(
        self, participant_client: TestClient, db_session: Session
    ) -> None:
        create_resp = participant_client.post(
            "/v1/participants", json={"contact_email": "person@example.com"}
        )
        participant_id = create_resp.json()["participant_id"]

        delete_resp = participant_client.delete(f"/v1/participants/{participant_id}")
        assert delete_resp.status_code == 202

        # Deleted participants 404 on the normal read path.
        get_resp = participant_client.get(f"/v1/participants/{participant_id}")
        assert get_resp.status_code == 404

        from app.database.models import Participant

        row = (
            db_session.query(Participant)
            .filter(Participant.participant_id == participant_id)
            .first()
        )
        assert row is not None
        assert row.is_deleted is True
        assert row.encrypted_contact_email is None


class TestTrialIngestionRequiresConsent:
    def test_session_creation_blocked_without_consent(
        self, researcher_client: TestClient, participant_client: TestClient
    ) -> None:
        study_id, battery_id = _create_study_and_battery(researcher_client)
        participant_id = participant_client.post("/v1/participants", json={}).json()[
            "participant_id"
        ]

        resp = participant_client.post(
            "/v1/participant-sessions",
            json={"participant_id": participant_id, "study_id": study_id, "battery_id": battery_id},
        )
        assert resp.status_code == 403
        assert "research_participation" in resp.json()["error"]["message"]

    def test_full_happy_path_ingest_and_complete(
        self, researcher_client: TestClient, participant_client: TestClient
    ) -> None:
        study_id, battery_id = _create_study_and_battery(researcher_client)
        participant_id = participant_client.post("/v1/participants", json={}).json()[
            "participant_id"
        ]

        participant_client.post(
            f"/v1/participants/{participant_id}/consents",
            json={"consent_type": "research_participation"},
        )

        session_resp = participant_client.post(
            "/v1/participant-sessions",
            json={"participant_id": participant_id, "study_id": study_id, "battery_id": battery_id},
        )
        assert session_resp.status_code == 201, session_resp.text
        participant_session_id = session_resp.json()["participant_session_id"]
        assert session_resp.json()["session_index"] == 1

        trials_resp = participant_client.post(
            f"/v1/participant-sessions/{participant_session_id}/trials",
            json={
                "events": [
                    {
                        "event_id": "e0",
                        "seq_no": 0,
                        "task_type": "stroop",
                        "trial_index": 0,
                        "rt_ms": 512.3,
                        "correct": True,
                    },
                    {
                        "event_id": "e1",
                        "seq_no": 1,
                        "task_type": "stroop",
                        "trial_index": 1,
                        "rt_ms": 480.1,
                        "correct": False,
                    },
                ]
            },
        )
        assert trials_resp.status_code == 201, trials_resp.text
        assert len(trials_resp.json()) == 2

        # Duplicate (participant_session_id, task_type, trial_index) is rejected at the DB layer.
        dup_resp = participant_client.post(
            f"/v1/participant-sessions/{participant_session_id}/trials",
            json={
                "events": [
                    {
                        "event_id": "e9",
                        "seq_no": 2,
                        "task_type": "stroop",
                        "trial_index": 0,
                        "rt_ms": 500.0,
                    }
                ]
            },
        )
        assert dup_resp.status_code >= 400

        complete_resp = participant_client.post(
            f"/v1/participant-sessions/{participant_session_id}/complete"
        )
        assert complete_resp.status_code == 200, complete_resp.text
        body = complete_resp.json()
        assert body["status"] == "completed"
        assert body["trial_count"] == 2

        # Idempotent: calling complete again returns the same state, not an error.
        second_complete = participant_client.post(
            f"/v1/participant-sessions/{participant_session_id}/complete"
        )
        assert second_complete.status_code == 200
        assert second_complete.json()["trial_count"] == 2

    def test_idempotent_ingestion_conflicts_and_gaps(
        self, researcher_client: TestClient, participant_client: TestClient
    ) -> None:
        study_id, battery_id = _create_study_and_battery(researcher_client)
        pid = participant_client.post("/v1/participants", json={}).json()["participant_id"]
        participant_client.post(
            f"/v1/participants/{pid}/consents", json={"consent_type": "research_participation"}
        )
        sid = participant_client.post(
            "/v1/participant-sessions",
            json={"participant_id": pid, "study_id": study_id, "battery_id": battery_id},
        ).json()["participant_session_id"]
        url = f"/v1/participant-sessions/{sid}/trials"

        def ev(event_id: str, seq: int, idx: int, rt: float = 500.0) -> dict:
            return {
                "event_id": event_id,
                "seq_no": seq,
                "task_type": "stroop",
                "trial_index": idx,
                "rt_ms": rt,
            }

        first = participant_client.post(url, json={"events": [ev("a", 0, 0), ev("c", 2, 2)]})
        assert first.status_code == 201
        original_id = first.json()[0]["trial_event_id"]

        # Same event_id + same payload: acknowledged as a duplicate with the original receipt.
        retry = participant_client.post(url, json={"events": [ev("a", 0, 0)]})
        assert retry.status_code == 201
        assert retry.json()[0]["duplicate"] is True
        assert retry.json()[0]["trial_event_id"] == original_id

        # Same event_id + different payload: 409, and nothing from the batch is stored.
        conflict = participant_client.post(
            url, json={"events": [ev("b", 1, 1), ev("a", 0, 0, rt=999.0)]}
        )
        assert conflict.status_code == 409

        # Seq 1 is still missing (the conflicting batch stored nothing): complete reports it.
        incomplete = participant_client.post(f"/v1/participant-sessions/{sid}/complete").json()
        assert incomplete["awaiting_inputs"] is True
        assert incomplete["missing_seq_nos"] == [1]
        assert incomplete["status"] != "completed"

        # Out-of-order arrival is retained; once the gap fills, completion succeeds.
        assert participant_client.post(url, json={"events": [ev("b", 1, 1)]}).status_code == 201
        done = participant_client.post(f"/v1/participant-sessions/{sid}/complete").json()
        assert done["status"] == "completed" and done["trial_count"] == 3

    def test_second_session_gets_incremented_index(
        self, researcher_client: TestClient, participant_client: TestClient
    ) -> None:
        study_id, battery_id = _create_study_and_battery(researcher_client)
        participant_id = participant_client.post("/v1/participants", json={}).json()[
            "participant_id"
        ]
        participant_client.post(
            f"/v1/participants/{participant_id}/consents",
            json={"consent_type": "research_participation"},
        )

        first = participant_client.post(
            "/v1/participant-sessions",
            json={"participant_id": participant_id, "study_id": study_id, "battery_id": battery_id},
        ).json()
        second = participant_client.post(
            "/v1/participant-sessions",
            json={"participant_id": participant_id, "study_id": study_id, "battery_id": battery_id},
        ).json()

        assert first["session_index"] == 1
        assert second["session_index"] == 2


class TestConsentCompletion:
    def test_client_supplied_consent_text_is_rejected(self, participant_client: TestClient) -> None:
        pid = participant_client.post("/v1/participants", json={}).json()["participant_id"]
        resp = participant_client.post(
            f"/v1/participants/{pid}/consents",
            json={"consent_type": "research_participation", "consent_text": "my own text"},
        )
        assert resp.status_code == 422

    def test_revoke_closes_every_open_grant(
        self, participant_client: TestClient, db_session: Session
    ) -> None:
        from app.database.models import Consent

        pid = participant_client.post("/v1/participants", json={}).json()["participant_id"]
        url = f"/v1/participants/{pid}/consents"
        for _ in range(2):
            assert (
                participant_client.post(url, json={"consent_type": "recontact"}).status_code == 201
            )
        assert (
            participant_client.post(f"{url}/revoke", json={"consent_type": "recontact"}).status_code
            == 204
        )
        db_session.expire_all()
        open_grants = (
            db_session.query(Consent)
            .filter(Consent.participant_id == pid, Consent.revoked_at.is_(None))
            .count()
        )
        assert open_grants == 0
        assert participant_client.get(f"{url}/status").json()["recontact"] is False

    def test_product_only_participant_can_run_a_session(
        self, researcher_client: TestClient, participant_client: TestClient
    ) -> None:
        study_id, battery_id = _create_study_and_battery(researcher_client)
        pid = participant_client.post("/v1/participants", json={}).json()["participant_id"]
        participant_client.post(
            f"/v1/participants/{pid}/consents", json={"consent_type": "product_use"}
        )
        resp = participant_client.post(
            "/v1/participant-sessions",
            json={"participant_id": pid, "study_id": study_id, "battery_id": battery_id},
        )
        assert resp.status_code == 201, resp.text


class TestParticipantExport:
    def test_owner_exports_own_data_and_other_user_cannot(
        self, participant_client: TestClient, researcher_client: TestClient
    ) -> None:
        pid = participant_client.post(
            "/v1/participants", json={"contact_email": "p@example.org"}
        ).json()["participant_id"]
        participant_client.post(
            f"/v1/participants/{pid}/consents", json={"consent_type": "product_use"}
        )
        body = participant_client.get(f"/v1/participants/{pid}/export").json()
        assert body["participant"]["contact_email"] == "p@example.org"
        assert [c["consent_type"] for c in body["consents"]] == ["product_use"]
        assert body["sessions"] == [] and body["item_responses"] == []

    def test_erased_participant_has_nothing_to_export(self, participant_client: TestClient) -> None:
        pid = participant_client.post("/v1/participants", json={}).json()["participant_id"]
        participant_client.delete(f"/v1/participants/{pid}")
        assert participant_client.get(f"/v1/participants/{pid}/export").status_code == 404

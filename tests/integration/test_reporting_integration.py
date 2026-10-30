"""
Integration tests for Phase 3-4: scoring on session completion, /v1/norms,
/v1/instrument/psychometrics, /v1/meta, and /v1/dataset-card.
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
from app.services.identifiers import load_identifiers


def _make_user(roles: list[str], user_id: str) -> TokenPayload:
    return TokenPayload(
        user_id=user_id,
        username=f"{user_id}@example.com",
        roles=roles,
        exp=datetime.now(timezone.utc) + timedelta(hours=1),
    )


@pytest.fixture
def db_engine() -> Generator[Any, None, None]:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def _override_get_db(engine: Any) -> Any:
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


def _client(db_engine: Any, roles: list[str], user_id: str) -> TestClient:
    app = create_app(test_mode=True)
    app.dependency_overrides[get_db] = _override_get_db(db_engine)
    app.dependency_overrides[get_current_user] = lambda: _make_user(roles, user_id)
    return TestClient(app, raise_server_exceptions=False)


def _run_one_session(
    researcher: TestClient,
    participant: TestClient,
    study_id: str,
    battery_id: str,
    accuracy_pattern: list[bool],
    db_engine: Any,
) -> str:
    """Create a participant, consent, a session, ingest trials matching
    `accuracy_pattern`, and complete the session.

    The completion endpoint enqueues scoring via Celery `.delay()`; the
    test environment's Celery mock (tests/conftest.py) returns the bare
    undecorated function, which has no `.delay`, so trials.py's best-effort
    enqueue silently no-ops (by design — a broken task queue must never
    fail session completion for the client). That mirrors production
    exactly for the completion contract itself, but means scoring has to be
    invoked directly here to test its output, the same way a Celery worker
    would in a real deployment.
    """
    participant_id = participant.post("/v1/participants", json={}).json()["participant_id"]
    participant.post(
        f"/v1/participants/{participant_id}/consents",
        json={"consent_type": "research_participation"},
    )
    session_resp = participant.post(
        "/v1/participant-sessions",
        json={"participant_id": participant_id, "study_id": study_id, "battery_id": battery_id},
    )
    assert session_resp.status_code == 201, session_resp.text
    participant_session_id = session_resp.json()["participant_session_id"]

    events = [
        {
            "event_id": f"e{i}",
            "seq_no": i,
            "task_type": "stroop",
            "trial_index": i,
            "rt_ms": 400 + i * 5,
            "correct": correct,
        }
        for i, correct in enumerate(accuracy_pattern)
    ]
    ingest_resp = participant.post(
        f"/v1/participant-sessions/{participant_session_id}/trials", json={"events": events}
    )
    assert ingest_resp.status_code == 201, ingest_resp.text

    quality_resp = participant.post(
        f"/v1/participant-sessions/{participant_session_id}/quality",
        json={
            "tier_config_version": "tier_config-1.0.0",
            "timing_errors_ms": [1.0, 2.0, 3.0],
            "fullscreen": True,
            "attention_checks_passed": 2,
        },
    )
    assert quality_resp.status_code == 200, quality_resp.text
    assert quality_resp.json()["device_tier"] == "A"

    complete_resp = participant.post(f"/v1/participant-sessions/{participant_session_id}/complete")
    assert complete_resp.status_code == 200, complete_resp.text

    from app.services.scoring import score_session

    SessionLocal = sessionmaker(bind=db_engine)
    db = SessionLocal()
    try:
        assert score_session(db, participant_session_id) is not None
    finally:
        db.close()

    return participant_session_id


class TestScoringOnCompletion:
    def test_completion_computes_scores_synchronously(self, db_engine: Any) -> None:
        researcher = _client(db_engine, ["researcher"], "researcher-1")
        participant = _client(db_engine, ["viewer"], "participant-1")

        study_id = researcher.post("/v1/studies", json={"name": "Scoring Study"}).json()["study_id"]
        battery_id = researcher.post(
            "/v1/batteries",
            json={"study_id": study_id, "name": "B", "version": "1.0", "form_label": "A"},
        ).json()["battery_id"]

        participant_session_id = _run_one_session(
            researcher, participant, study_id, battery_id, [True, True, True, False], db_engine
        )

        # Read back the session directly via the DB to check scores were persisted.
        SessionLocal = sessionmaker(bind=db_engine)
        db = SessionLocal()
        try:
            from app.database.models import ParticipantSession

            row = (
                db.query(ParticipantSession)
                .filter(ParticipantSession.participant_session_id == participant_session_id)
                .first()
            )
            assert row is not None
            assert row.scores is not None
            assert row.scores["tasks"]["stroop"]["n_trials"] == 4
            assert row.scores["tasks"]["stroop"]["accuracy"] == 0.75
            assert row.scores["form_label"] == "A"
            assert row.scores["session_index"] == 1
        finally:
            db.close()


class TestNormsAndPsychometrics:
    def test_norms_requires_scored_sessions(self, db_engine: Any) -> None:
        researcher = _client(db_engine, ["researcher"], "researcher-1")
        study_id = researcher.post("/v1/studies", json={"name": "Norms Study"}).json()["study_id"]
        battery_id = researcher.post(
            "/v1/batteries",
            json={"study_id": study_id, "name": "B", "version": "1.0", "form_label": "A"},
        ).json()["battery_id"]

        resp = researcher.get(
            "/v1/norms",
            params={
                "study_id": study_id,
                "battery_id": battery_id,
                "task_type": "stroop",
                "metric": "accuracy",
                "score": 0.8,
            },
        )
        assert resp.status_code == 404

    def test_norms_and_psychometrics_after_multiple_sessions(
        self, db_engine: Any, monkeypatch: Any
    ) -> None:
        from app.routes import norms

        # Patch the instance the route actually reads (immune to config reloads).
        monkeypatch.setattr(norms.settings, "norms_min_cell_size", 1)
        researcher = _client(db_engine, ["researcher"], "researcher-1")
        study_id = researcher.post("/v1/studies", json={"name": "Norms Study 2"}).json()["study_id"]
        battery_id = researcher.post(
            "/v1/batteries",
            json={"study_id": study_id, "name": "B2", "version": "1.0", "form_label": "A"},
        ).json()["battery_id"]

        patterns = [
            [True, True, True, True],
            [True, True, False, False],
            [True, False, False, False],
        ]
        for i, pattern in enumerate(patterns):
            participant = _client(db_engine, ["viewer"], f"participant-{i}")
            _run_one_session(researcher, participant, study_id, battery_id, pattern, db_engine)

        norms_resp = researcher.get(
            "/v1/norms",
            params={
                "study_id": study_id,
                "battery_id": battery_id,
                "task_type": "stroop",
                "metric": "accuracy",
                "score": 0.5,
            },
        )
        assert norms_resp.status_code == 200, norms_resp.text
        body = norms_resp.json()
        assert body["n"] == 3
        assert 0 <= body["percentile"] <= 100
        assert body["percentile_ci_lower"] <= body["percentile"] <= body["percentile_ci_upper"]
        assert body["release_state"] == "pre-registered"
        assert body["note"] is not None

        psych_resp = researcher.get(
            "/v1/instrument/psychometrics",
            params={"battery_id": battery_id, "task_type": "stroop"},
        )
        assert psych_resp.status_code == 200, psych_resp.text
        psych_body = psych_resp.json()
        assert psych_body["n_sessions"] == 3
        assert psych_body["n_items"] == 4
        assert isinstance(psych_body["cronbachs_alpha"], float)


class TestMetaAndDatasetCard:
    def test_meta_matches_identifiers_yaml(self, db_engine: Any) -> None:
        client = _client(db_engine, ["viewer"], "someone")
        resp = client.get("/v1/meta")
        assert resp.status_code == 200
        body = resp.json()
        ids = load_identifiers()
        assert body["researcher"]["orcid"] == ids["researcher"]["orcid"]
        assert body["zenodo"]["concept_doi"] == ids["zenodo"]["concept_doi"]
        assert body["release_state"]["current"] == ids["release_state"]["current"]

    def test_meta_is_public_without_auth_header(self, db_engine: Any) -> None:
        app = create_app(test_mode=False)
        app.dependency_overrides[get_db] = _override_get_db(db_engine)
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/v1/meta")
        assert resp.status_code == 200

    def test_dataset_card_surfaces_privacy_warning(self, db_engine: Any) -> None:
        client = _client(db_engine, ["viewer"], "someone")
        resp = client.get("/v1/dataset-card")
        assert resp.status_code == 200
        body = resp.json()
        assert any("Privacy review" in w for w in body["warnings"])
        assert body["counts"]["studies"] == 0


class TestNormsSuppression:
    def test_small_cell_returns_raw_value_only(self, db_engine: Any) -> None:
        researcher = _client(db_engine, ["researcher"], "researcher-1")
        study_id = researcher.post("/v1/studies", json={"name": "Sup"}).json()["study_id"]
        battery_id = researcher.post(
            "/v1/batteries",
            json={"study_id": study_id, "name": "B", "version": "1.0", "form_label": "A"},
        ).json()["battery_id"]
        participant = _client(db_engine, ["viewer"], "participant-0")
        _run_one_session(researcher, participant, study_id, battery_id, [True, False], db_engine)

        body = researcher.get(
            "/v1/norms",
            params={
                "study_id": study_id,
                "battery_id": battery_id,
                "task_type": "stroop",
                "metric": "accuracy",
                "score": 0.5,
            },
        ).json()
        assert body["suppressed"] is True
        assert body["min_cell_size"] == 200
        assert body["score"] == 0.5 and body["n"] == 1
        assert body["percentile"] is None and body["percentile_ci_lower"] is None


class TestReferenceDatasetCard:
    def test_unreleased_version_is_404_and_public(self, db_engine: Any) -> None:
        app = create_app(test_mode=False)
        app.dependency_overrides[get_db] = _override_get_db(db_engine)
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/v1/reference-datasets/0.1/card")
        assert resp.status_code == 404

    def test_released_version_serves_card_with_unrecorded_fields(
        self, db_engine: Any, monkeypatch: Any
    ) -> None:
        from app.routes import meta

        real = meta.load_identifiers()
        patched = {
            **real,
            "release_state": {**real["release_state"], "reference_dataset_version": "0.1"},
        }
        monkeypatch.setattr(meta, "load_identifiers", lambda: patched)
        body = _client(db_engine, [], "anon").get("/v1/reference-datasets/0.1/card").json()
        assert body["reference_dataset_version"] == "0.1"
        assert body["incentives"] == {"status": "not_recorded"}

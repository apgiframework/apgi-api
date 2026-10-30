"""
Integration tests for Phase 5: longitudinal metrics, MDC-gated change
reporting, and the n-of-1 experiment engine.
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
from app.services.scoring import score_session


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


def _consented_participant(client: TestClient) -> str:
    participant_id = client.post("/v1/participants", json={}).json()["participant_id"]
    client.post(
        f"/v1/participants/{participant_id}/consents",
        json={"consent_type": "research_participation"},
    )
    return participant_id


def _score_two_sessions(
    db_engine: Any,
    researcher: TestClient,
    participant: TestClient,
    participant_id: str,
    study_id: str,
    battery_id: str,
    accuracy_a: float,
    accuracy_b: float,
) -> None:
    n_trials = 10
    for accuracy in (accuracy_a, accuracy_b):
        n_correct = round(accuracy * n_trials)
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
                "correct": i < n_correct,
            }
            for i in range(n_trials)
        ]
        ingest_resp = participant.post(
            f"/v1/participant-sessions/{participant_session_id}/trials", json={"events": events}
        )
        assert ingest_resp.status_code == 201, ingest_resp.text

        complete_resp = participant.post(
            f"/v1/participant-sessions/{participant_session_id}/complete"
        )
        assert complete_resp.status_code == 200, complete_resp.text

        SessionLocal = sessionmaker(bind=db_engine)
        db = SessionLocal()
        try:
            assert score_session(db, participant_session_id) is not None
        finally:
            db.close()


class TestLongitudinalMetricsAndChangeReport:
    def test_metrics_require_at_least_two_paired_subjects(self, db_engine: Any) -> None:
        researcher = _client(db_engine, ["researcher"], "researcher-1")
        study_id = researcher.post("/v1/studies", json={"name": "Longitudinal Study"}).json()[
            "study_id"
        ]
        battery_id = researcher.post(
            "/v1/batteries", json={"study_id": study_id, "name": "B", "version": "1.0"}
        ).json()["battery_id"]

        resp = researcher.get(
            "/v1/longitudinal/metrics",
            params={
                "study_id": study_id,
                "battery_id": battery_id,
                "task_type": "stroop",
                "metric": "accuracy",
            },
        )
        assert resp.status_code == 404

    def test_metrics_and_change_report_with_paired_data(self, db_engine: Any) -> None:
        researcher = _client(db_engine, ["researcher"], "researcher-1")
        study_id = researcher.post("/v1/studies", json={"name": "Longitudinal Study 2"}).json()[
            "study_id"
        ]
        battery_id = researcher.post(
            "/v1/batteries", json={"study_id": study_id, "name": "B2", "version": "1.0"}
        ).json()["battery_id"]

        participant_ids = []
        for i, (a, b) in enumerate([(0.5, 0.9), (0.4, 0.5), (0.6, 0.65)]):
            participant = _client(db_engine, ["viewer"], f"participant-{i}")
            participant_id = _consented_participant(participant)
            _score_two_sessions(
                db_engine, researcher, participant, participant_id, study_id, battery_id, a, b
            )
            participant_ids.append((participant_id, participant))

        metrics_resp = researcher.get(
            "/v1/longitudinal/metrics",
            params={
                "study_id": study_id,
                "battery_id": battery_id,
                "task_type": "stroop",
                "metric": "accuracy",
            },
        )
        assert metrics_resp.status_code == 200, metrics_resp.text
        body = metrics_resp.json()
        assert body["n_subjects"] == 3
        assert 0.0 <= body["icc"] <= 1.0
        assert body["sem"] >= 0.0
        assert body["mdc95"] >= 0.0

        # First participant had the largest jump (0.5 -> 0.9); check their change report.
        first_participant_id, first_participant_client = participant_ids[0]
        change_resp = first_participant_client.get(
            f"/v1/participants/{first_participant_id}/change-report",
            params={
                "study_id": study_id,
                "battery_id": battery_id,
                "task_type": "stroop",
                "metric": "accuracy",
            },
        )
        # No active subscription -> 402, since this is a non-staff participant.
        assert change_resp.status_code == 402

    def test_researcher_can_get_change_report_without_subscription(self, db_engine: Any) -> None:
        researcher = _client(db_engine, ["researcher"], "researcher-1")
        study_id = researcher.post("/v1/studies", json={"name": "Longitudinal Study 3"}).json()[
            "study_id"
        ]
        battery_id = researcher.post(
            "/v1/batteries", json={"study_id": study_id, "name": "B3", "version": "1.0"}
        ).json()["battery_id"]

        for i, (a, b) in enumerate([(0.5, 0.9), (0.4, 0.5)]):
            participant = _client(db_engine, ["viewer"], f"p{i}")
            participant_id = _consented_participant(participant)
            _score_two_sessions(
                db_engine, researcher, participant, participant_id, study_id, battery_id, a, b
            )

        # Get the first participant's id via researcher listing isn't exposed;
        # re-derive by creating one more directly-observed participant instead.
        participant = _client(db_engine, ["viewer"], "px")
        participant_id = _consented_participant(participant)
        _score_two_sessions(
            db_engine, researcher, participant, participant_id, study_id, battery_id, 0.5, 0.95
        )

        resp = researcher.get(
            f"/v1/participants/{participant_id}/change-report",
            params={
                "study_id": study_id,
                "battery_id": battery_id,
                "task_type": "stroop",
                "metric": "accuracy",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["classification"] in {
            "reliable_increase",
            "reliable_decrease",
            "no_reliable_change",
        }
        assert body["score_b"] > body["score_a"]


class TestNOf1Engine:
    def test_full_experiment_lifecycle(self, db_engine: Any) -> None:
        participant_client = _client(db_engine, ["viewer"], "participant-1")
        participant_id = participant_client.post("/v1/participants", json={}).json()[
            "participant_id"
        ]

        create_resp = participant_client.post(
            "/v1/n-of-1/experiments",
            json={
                "participant_id": participant_id,
                "name": "Sleep vs mood",
                "phase_sequence": ["A", "B", "A", "B"],
                "outcome_metric_name": "mood_rating",
            },
        )
        assert create_resp.status_code == 201, create_resp.text
        experiment_id = create_resp.json()["experiment_id"]

        # Reject a phase label not in the declared sequence.
        bad_resp = participant_client.post(
            f"/v1/n-of-1/experiments/{experiment_id}/observations",
            json={"phase_label": "C", "value": 5.0},
        )
        assert bad_resp.status_code == 400

        a_values = [3.0, 4.0, 3.5, 4.5]
        b_values = [7.0, 8.0, 7.5, 8.5]
        for v in a_values:
            resp = participant_client.post(
                f"/v1/n-of-1/experiments/{experiment_id}/observations",
                json={"phase_label": "A", "value": v},
            )
            assert resp.status_code == 201
        for v in b_values:
            resp = participant_client.post(
                f"/v1/n-of-1/experiments/{experiment_id}/observations",
                json={"phase_label": "B", "value": v},
            )
            assert resp.status_code == 201

        analysis_resp = participant_client.get(f"/v1/n-of-1/experiments/{experiment_id}/analysis")
        assert analysis_resp.status_code == 200, analysis_resp.text
        body = analysis_resp.json()
        assert body["n_observations"] == 8
        phase_a = next(p for p in body["phase_summaries"] if p["phase_label"] == "A")
        phase_b = next(p for p in body["phase_summaries"] if p["phase_label"] == "B")
        assert phase_a["n"] == 4
        assert phase_b["mean"] > phase_a["mean"]
        assert body["contrast"] is not None
        assert body["contrast"]["mean_difference"] > 0

    def test_other_user_cannot_access_experiment(self, db_engine: Any) -> None:
        owner = _client(db_engine, ["viewer"], "owner-1")
        participant_id = owner.post("/v1/participants", json={}).json()["participant_id"]
        experiment_id = owner.post(
            "/v1/n-of-1/experiments",
            json={
                "participant_id": participant_id,
                "name": "Private experiment",
                "phase_sequence": ["A", "B"],
                "outcome_metric_name": "x",
            },
        ).json()["experiment_id"]

        other = _client(db_engine, ["viewer"], "other-1")
        resp = other.get(f"/v1/n-of-1/experiments/{experiment_id}")
        assert resp.status_code == 403

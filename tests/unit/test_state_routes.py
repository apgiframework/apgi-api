"""
Comprehensive tests for state routes (app/routes/state.py).

Tests cover all 5 endpoints with success and error paths:
1. GET /{session_id}/state - Complete system state
2. GET /{session_id}/ignition-history - Ignition events with pagination
3. GET /{session_id}/interoception - Body state
4. GET /{session_id}/prediction-errors - Prediction errors
5. GET /{session_id}/somatic-markers - Somatic markers
"""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database.models import Session as SessionModel
from app.models.schemas import TokenPayload
from app.services.authorization import Permission, require_permission  # noqa: F401

FAKE_USER = TokenPayload(
    user_id="user-123",
    username="testuser",
    roles=["admin"],
    exp=datetime.now(timezone.utc) + timedelta(hours=1),
    token_type="access",
    permissions=[],
)


@asynccontextmanager
async def noop_lifespan(app: Any) -> AsyncIterator[Any]:
    yield


def _make_session_model(session_id: str = "sess-1", user_id: str = "user-123") -> MagicMock:
    """Create a mock SessionModel."""
    m = MagicMock(spec=SessionModel)
    m.session_id = session_id
    m.user_id = user_id
    m.is_deleted = False
    return m


def _make_sim_session(session_id: str = "sess-1") -> MagicMock:
    """Create a mock simulation session."""
    s = MagicMock()
    s.session_id = session_id
    s.get_state = AsyncMock()
    return s


def _make_query_chain(session_model: MagicMock) -> MagicMock:
    """Create a mock query chain that returns the session_model."""
    q = MagicMock()
    q.filter.return_value.filter.return_value.first.return_value = session_model
    return q


@pytest.fixture
def mock_db() -> MagicMock:
    return MagicMock(spec=Session)


@pytest.fixture
def mock_manager() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def client(mock_db: MagicMock, mock_manager: AsyncMock) -> TestClient:
    from app.database.connection import get_db
    from app.main import create_app
    from app.routes.sessions import get_session_manager
    from app.services.authorization import get_current_user

    app = create_app(test_mode=True)
    app.router.lifespan_context = noop_lifespan
    app.dependency_overrides[get_db] = lambda: mock_db
    app.dependency_overrides[get_current_user] = lambda: FAKE_USER
    app.dependency_overrides[get_session_manager] = lambda: mock_manager
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Tests for GET /{session_id}/state
# ---------------------------------------------------------------------------


class TestGetSystemState:
    """Tests for the GET /{session_id}/state endpoint."""

    def _setup_db(self, mock_db: MagicMock, user_id: str = "user-123") -> None:
        """Setup mock database with session."""
        session_model = _make_session_model(user_id=user_id)
        mock_db.query.return_value.filter.return_value.filter.return_value.first.return_value = (
            session_model
        )

    def test_get_system_state_success(
        self, client: TestClient, mock_manager: AsyncMock, mock_db: MagicMock
    ) -> None:
        """Test successful retrieval of complete system state."""
        self._setup_db(mock_db)
        sim_session = _make_sim_session()
        sim_session.get_state.return_value = {
            "time": 1000.0,
            "ignition": {
                "ignition_occurred": True,
                "total_signal": 2.5,
                "threshold": 2.0,
                "duration_ms": 500.0,
            },
            "workspace": {
                "is_broadcasting": True,
                "content": "test content",
                "broadcast_duration_ms": 1000.0,
            },
            "body": {
                "heart_rate": 85.0,
                "cortisol": 0.15,
                "temperature": 37.5,
            },
            "allostasis": {"allostatic_load": 0.6},
            "precision": {
                "exteroceptive": 0.9,
                "interoceptive": 0.8,
            },
            "metabolism": {
                "reserves": 950.0,
                "reserve_fraction": 0.95,
            },
            "self_model": {
                "minimal": {"coherence": 0.7},
                "narrative": {"narrative": "I am aware"},
            },
        }
        mock_manager.get_session.return_value = sim_session

        resp = client.get("/v1/sessions/sess-1/state")
        assert resp.status_code == 200
        data = resp.json()
        assert data["time_ms"] == 1000.0
        assert data["ignition"]["ignition_occurred"] is True
        assert data["workspace"]["is_broadcasting"] is True
        # K7-gated fields are withheld by default (governing doc §3.6):
        # absent from the response, not present as null.
        assert "threshold" not in data["ignition"]
        assert data["body"] == {}
        assert data["precision"] == {}

    def test_get_system_state_k7_cleared_parameters_exposed(
        self,
        client: TestClient,
        mock_manager: AsyncMock,
        mock_db: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Once a parameter clears K7, its field is present with the real value."""
        from app.services import k7_gate

        monkeypatch.setattr(
            k7_gate.settings,
            "k7_cleared_parameters",
            {
                "ignition_threshold",
                "precision_exteroceptive",
                "precision_interoceptive",
                "interoceptive_body_state",
            },
        )
        self._setup_db(mock_db)
        sim_session = _make_sim_session()
        sim_session.get_state.return_value = {
            "time": 1000.0,
            "ignition": {"ignition_occurred": True, "total_signal": 2.5, "threshold": 2.0},
            "body": {"heart_rate": 85.0, "cortisol": 0.15, "temperature": 37.5},
            "precision": {"exteroceptive": 0.9, "interoceptive": 0.8},
        }
        mock_manager.get_session.return_value = sim_session

        resp = client.get("/v1/sessions/sess-1/state")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ignition"]["threshold"] == 2.0
        assert data["body"]["heart_rate"] == 85.0
        assert data["precision"]["exteroceptive"] == 0.9
        assert data["precision"]["interoceptive"] == 0.8

    def test_get_system_state_with_defaults(
        self, client: TestClient, mock_manager: AsyncMock, mock_db: MagicMock
    ) -> None:
        """Test state retrieval with missing fields uses defaults."""
        self._setup_db(mock_db)
        sim_session = _make_sim_session()
        sim_session.get_state.return_value = {}
        mock_manager.get_session.return_value = sim_session

        resp = client.get("/v1/sessions/sess-1/state")
        assert resp.status_code == 200
        data = resp.json()
        assert data["time_ms"] == 0.0
        assert data["ignition"]["ignition_occurred"] is False
        assert data["body"] == {}

    def test_get_system_state_session_not_found_in_db(
        self, client: TestClient, mock_db: MagicMock
    ) -> None:
        """Test 404 when session not found in database."""
        mock_db.query.return_value.filter.return_value.filter.return_value.first.return_value = None
        resp = client.get("/v1/sessions/nonexistent/state")
        assert resp.status_code == 404
        # Check if response has detail key or alternative error format
        response_data = resp.json()
        assert "detail" in response_data or "message" in response_data or "error" in response_data
        if "detail" in response_data:
            assert "not found" in response_data["detail"].lower()

    def test_get_system_state_session_not_found_in_manager(
        self, client: TestClient, mock_manager: AsyncMock, mock_db: MagicMock
    ) -> None:
        """Test 404 when session not found in manager."""
        self._setup_db(mock_db)
        mock_manager.get_session.side_effect = ValueError("Session not found")
        resp = client.get("/v1/sessions/sess-1/state")
        assert resp.status_code == 404

    def test_get_system_state_generic_error(
        self, client: TestClient, mock_manager: AsyncMock, mock_db: MagicMock
    ) -> None:
        """Test 500 on generic exception."""
        self._setup_db(mock_db)
        sim_session = _make_sim_session()
        sim_session.get_state.side_effect = Exception("Unexpected error")
        mock_manager.get_session.return_value = sim_session
        resp = client.get("/v1/sessions/sess-1/state")
        assert resp.status_code == 500
        # Check if response has detail key or alternative error format
        response_data = resp.json()
        assert "detail" in response_data or "message" in response_data or "error" in response_data
        if "detail" in response_data:
            assert "internal error" in response_data["detail"].lower()


# ---------------------------------------------------------------------------
# Tests for GET /{session_id}/ignition-history
# ---------------------------------------------------------------------------


GATED_ROUTES = [
    "ignition-history",
    "interoception",
    "prediction-errors",
    "somatic-markers",
    "state-vector",
]


class TestGatedLatentRoutes:
    """The five latent routes return identifiability-gate state, never values (PR 2b)."""

    @pytest.mark.parametrize("route", GATED_ROUTES)
    def test_returns_gate_state_only(
        self, client: TestClient, mock_db: MagicMock, mock_manager: AsyncMock, route: str
    ) -> None:
        mock_db.query.return_value.filter.return_value.filter.return_value.first.return_value = (
            _make_session_model()
        )
        sim = _make_sim_session()
        sim.get_state.return_value = {"ignition": {"threshold": 2.0}, "body": {"heart_rate": 85.0}}
        mock_manager.get_session.return_value = sim

        response = client.get(f"/v1/sessions/sess-1/{route}")

        assert response.status_code == 200
        data = response.json()
        assert set(data) == {"session_id", "identifiability_gate", "gate_table_version"}
        assert data["identifiability_gate"]
        assert all(
            g["exposed"] is False and g["status"] == "withheld"
            for g in data["identifiability_gate"]
        )
        assert "2.0" not in response.text and "85.0" not in response.text
        sim.get_state.assert_not_called()

    @pytest.mark.parametrize("route", GATED_ROUTES)
    def test_unknown_session_is_404(
        self, client: TestClient, mock_db: MagicMock, route: str
    ) -> None:
        mock_db.query.return_value.filter.return_value.filter.return_value.first.return_value = None
        assert client.get(f"/v1/sessions/missing/{route}").status_code == 404

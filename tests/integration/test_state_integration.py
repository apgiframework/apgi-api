"""
Integration Tests for State Routes

Tests state access endpoints through HTTP requests with authentication.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.orm import Session


@pytest.fixture
async def authenticated_client(
    test_environment: None, mock_database_connection: None
) -> AsyncGenerator[AsyncClient, None]:
    """Create authenticated test client for state integration tests."""
    from unittest.mock import AsyncMock

    from app.main import create_app
    from app.models.schemas import TokenPayload
    from app.services.auth_manager import AuthManager

    # Mock Redis for the lifespan
    mock_redis_client = AsyncMock()
    mock_redis_client.ping = AsyncMock()
    mock_redis_client.aclose = AsyncMock()
    mock_redis_client.get = AsyncMock(return_value=None)

    # Create mock session manager
    mock_session_mgr = MagicMock()
    mock_session = MagicMock()
    mock_session.get_state = AsyncMock(
        return_value={
            "time": 1000.0,
            "ignition": {
                "ignition_occurred": True,
                "total_signal": 3.5,
                "threshold": 2.0,
                "duration_ms": 250.0,
            },
            "workspace": {
                "is_broadcasting": True,
                "content": "Test content",
                "broadcast_duration_ms": 150.0,
            },
            "body": {
                "heart_rate": 75.0,
                "cortisol": 0.15,
                "temperature": 37.2,
            },
            "allostasis": {
                "allostatic_load": 0.3,
            },
            "precision": {
                "exteroceptive": 0.8,
                "interoceptive": 0.9,
            },
            "metabolism": {
                "reserves": 800.0,
                "reserve_fraction": 0.8,
            },
            "self_model": {
                "minimal": {"coherence": 0.6},
                "narrative": {"active": True},
            },
            "history": {
                "time": [500.0, 1000.0, 1500.0],
                "ignitions": [False, True, False],
            },
            "prediction": {
                "errors": {"level1": 0.1, "level2": 0.05},
                "exteroceptive_stats": {"mean": 0.2},
                "interoceptive_stats": {"mean": 0.1},
            },
            "interoception": {
                "somatic_markers": {
                    "num_markers": 10,
                    "total_retrievals": 50,
                    "successful_retrievals": 45,
                    "retrieval_rate": 0.9,
                    "markers": [{"context": "test", "action": "response", "outcome": "good"}],
                },
            },
        }
    )
    mock_session_mgr.get_session = AsyncMock(return_value=mock_session)

    # Set the global session manager
    from app.routes import sessions

    sessions._state.session_manager = mock_session_mgr

    # Mock User for authentication
    mock_user = MagicMock()
    mock_user.user_id = str(uuid.uuid4())
    mock_user.username = "test_user"
    mock_user.roles = ["admin"]

    # Create a real JWT token for the mock user
    mock_session_db = MagicMock(spec=Session)
    auth_manager = AuthManager(db=mock_session_db)  # Use mock session for token creation
    access_token = auth_manager.create_access_token(
        user_id=mock_user.user_id, username=mock_user.username, roles=mock_user.roles
    )

    with (
        patch("redis.asyncio.from_url", return_value=mock_redis_client),
        patch("app.services.auth_manager.AuthManager.authenticate_user", return_value=mock_user),
        patch("app.middleware.authentication.is_authenticated", return_value=True),
        patch("app.services.authorization.check_permission", return_value=None),
        patch("app.services.authorization.has_permission", return_value=True),
        patch("app.services.authorization.require_permission", return_value=None),
        patch(
            "app.services.authorization.get_current_user",
            return_value=TokenPayload(
                user_id=mock_user.user_id,
                username=mock_user.username,
                roles=mock_user.roles,
                token_type="access",
                exp=datetime.now(timezone.utc) + timedelta(hours=1),
            ),
        ),
        patch("app.routes.sessions.get_session_manager", return_value=mock_session_mgr),
        patch("app.routes.state.validate_session_ownership", return_value=None),
        patch("app.routes.state.require_permission", return_value=None),
    ):
        app = create_app(test_mode=True)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Add the JWT token to default headers
            ac.headers.update({"Authorization": f"Bearer {access_token}"})
            # Store the mock redis for use in tests
            ac._mock_redis = mock_redis_client  # type: ignore[attr-defined]
            yield ac


class TestStateRoutesIntegration:
    """Integration tests for state access endpoints."""

    @pytest.mark.asyncio
    async def test_get_system_state_authenticated(self, authenticated_client: AsyncClient) -> None:
        """Test getting complete system state with authentication."""
        session_id = str(uuid.uuid4())

        response = await authenticated_client.get(f"/v1/sessions/{session_id}/state")

        assert response.status_code == 200
        data = response.json()
        assert "time_ms" in data
        assert "ignition" in data
        assert "workspace" in data
        assert "body" in data
        assert "allostasis" in data
        assert "precision" in data
        assert "metabolism" in data
        assert "self_model" in data

    @pytest.mark.asyncio
    async def test_get_ignition_history_authenticated(
        self, authenticated_client: AsyncClient
    ) -> None:
        """Test getting ignition event history with authentication."""
        session_id = str(uuid.uuid4())

        response = await authenticated_client.get(f"/v1/sessions/{session_id}/ignition-history")

        assert response.status_code == 200
        data = response.json()
        assert data["identifiability_gate"]
        assert "events" not in data

    @pytest.mark.asyncio
    async def test_get_interoceptive_state_authenticated(
        self, authenticated_client: AsyncClient
    ) -> None:
        """Test getting interoceptive body state with authentication."""
        session_id = str(uuid.uuid4())

        response = await authenticated_client.get(f"/v1/sessions/{session_id}/interoception")

        assert response.status_code == 200
        data = response.json()
        assert all(not g["exposed"] for g in data["identifiability_gate"])

    @pytest.mark.asyncio
    async def test_get_prediction_errors_authenticated(
        self, authenticated_client: AsyncClient
    ) -> None:
        """Test getting prediction errors with authentication."""
        session_id = str(uuid.uuid4())

        response = await authenticated_client.get(f"/v1/sessions/{session_id}/prediction-errors")

        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data
        assert all(not g["exposed"] for g in data["identifiability_gate"])
        assert "prediction_errors" not in data

    @pytest.mark.asyncio
    async def test_get_somatic_markers_authenticated(
        self, authenticated_client: AsyncClient
    ) -> None:
        """Test getting somatic markers with authentication."""
        session_id = str(uuid.uuid4())

        response = await authenticated_client.get(f"/v1/sessions/{session_id}/somatic-markers")

        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data
        assert all(not g["exposed"] for g in data["identifiability_gate"])
        assert "num_markers" not in data
        assert "markers" not in data

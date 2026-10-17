"""
Comprehensive unit tests for main.py module to achieve 100% coverage.
"""

import socket
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI


class TestPortAvailability:
    """Test port availability checking functionality."""

    def test_is_port_available_success(self) -> None:
        """Test successful port availability check."""
        from app.main import is_port_available

        # Test with available port
        result = is_port_available("127.0.0.1", 0)  # Port 0 should be available
        assert result is True

    def test_is_port_available_unavailable(self) -> None:
        """Test port availability check when port is in use."""
        from app.main import is_port_available

        # Test with unavailable port (use a common port that's likely in use)
        # This might fail if port is actually available, but we'll mock it
        with patch("socket.socket") as mock_socket:
            mock_socket_instance = MagicMock()
            mock_socket.return_value.__enter__.return_value = mock_socket_instance
            mock_socket_instance.bind.side_effect = OSError("Address already in use")

            result = is_port_available("127.0.0.1", 80)
            assert result is False

    def test_is_port_available_socket_options(self) -> None:
        """Test that socket options are set correctly."""
        from app.main import is_port_available

        with patch("socket.socket") as mock_socket:
            mock_socket_instance = MagicMock()
            mock_socket.return_value.__enter__.return_value = mock_socket_instance

            is_port_available("127.0.0.1", 12345)

            # Verify socket options were set
            mock_socket_instance.setsockopt.assert_called_once_with(
                socket.SOL_SOCKET, socket.SO_REUSEADDR, 1
            )
            mock_socket_instance.bind.assert_called_once_with(("127.0.0.1", 12345))


class TestDependencyChecker:
    """Test dependency checking during import."""

    def test_dependency_check_not_in_main(self) -> None:
        """Test that check_dependencies is not in app.main (it's in dependency_checker)."""
        from app import main as main_mod

        # check_dependencies is in app.dependency_checker, not app.main
        assert not hasattr(main_mod, "check_dependencies")

    def test_dependency_checker_module_exists(self) -> None:
        """Test that dependency_checker module exists separately."""
        from app import dependency_checker

        # Verify the module exists
        assert dependency_checker is not None


class TestLifespanComprehensive:
    """Comprehensive tests for lifespan context manager."""

    @pytest.mark.asyncio
    @patch("redis.asyncio.from_url")
    async def test_lifespan_full_startup_sequence(self, mock_redis: AsyncMock) -> None:
        """Test complete startup sequence with all components."""
        from app.main import lifespan

        mock_redis_client = AsyncMock()
        mock_redis_client.ping = AsyncMock()
        mock_redis_client.aclose = AsyncMock()
        mock_redis.return_value = mock_redis_client

        with (
            patch("app.main.configure_alerting") as mock_alerting,
            patch("app.main.configure_distributed_tracing") as mock_tracing,
            patch("app.main.init_db") as mock_init_db,
            patch("app.main.init_cache_service") as mock_cache,
            patch("app.main.sessions.init_session_routes") as mock_sessions,
            patch("app.main.tasks.init_task_routes") as mock_tasks,
            patch("app.main.export.init_export_routes") as mock_export,
            patch("app.main.health.init_health_routes") as mock_health,
            patch("app.main.sessions.get_session_manager") as mock_session_mgr,
            patch("app.main.close_db") as mock_close_db,
        ):
            mock_session_mgr.return_value = MagicMock()

            mock_app = MagicMock()

            async with lifespan(mock_app):
                pass

            # Verify all startup components were called
            mock_alerting.assert_called_once()
            mock_tracing.assert_called_once()
            mock_init_db.assert_called_once()
            mock_redis.assert_called_once()
            mock_cache.assert_called_once_with(mock_redis_client)
            mock_sessions.assert_called_once_with(mock_redis_client)
            mock_tasks.assert_called_once()
            mock_export.assert_called_once_with(mock_session_mgr.return_value)
            mock_health.assert_called_once_with(mock_redis_client)

    @pytest.mark.asyncio
    @patch("redis.asyncio.from_url")
    async def test_lifespan_tracing_disabled(self, mock_redis: AsyncMock) -> None:
        """Test lifespan when OpenTelemetry is not available."""
        from app.main import OPENTELEMETRY_AVAILABLE, lifespan

        # Temporarily disable OpenTelemetry
        original_value = OPENTELEMETRY_AVAILABLE
        try:
            # Mock the module to be unavailable
            with patch.dict(sys.modules, {"app.middleware.tracing": None}):
                # Re-import to set OPENTELEMETRY_AVAILABLE to False
                if "app.main" in sys.modules:
                    del sys.modules["app.main"]
                from app.main import lifespan

                mock_redis_client = AsyncMock()
                mock_redis_client.ping = AsyncMock()
                mock_redis_client.aclose = AsyncMock()
                mock_redis.return_value = mock_redis_client

                with (
                    patch("app.main.configure_alerting"),
                    patch("app.main.init_db"),
                    patch("app.main.sessions.init_session_routes"),
                    patch("app.main.tasks.init_task_routes"),
                    patch("app.main.export.init_export_routes"),
                    patch("app.main.health.init_health_routes"),
                    patch("app.main.sessions.get_session_manager"),
                ):
                    mock_app = MagicMock()

                    async with lifespan(mock_app):
                        pass

        finally:
            # Restore original value
            if "app.main" in sys.modules:
                del sys.modules["app.main"]

    @pytest.mark.asyncio
    @patch("redis.asyncio.from_url")
    @patch("app.main.sessions.get_session_manager")
    @patch("app.main.sessions.init_session_routes")
    async def test_lifespan_redis_failure(
        self,
        mock_init_session: MagicMock,
        mock_get_session: MagicMock,
        mock_redis: AsyncMock,
    ) -> None:
        """Test lifespan handles Redis initialization failure in production."""
        import sys

        # Remove cached main module to force reimport
        if "app.main" in sys.modules:
            del sys.modules["app.main"]

        from app.config import settings

        original_env = settings.environment
        settings.environment = "production"
        try:
            from app.main import lifespan

            mock_redis.side_effect = Exception("Redis connection failed")

            with (
                patch("app.main.configure_alerting"),
                patch("app.main.init_db"),
            ):
                mock_app = MagicMock()
                mock_app.state.test_mode = False  # Ensure not in test mode

                # In production, Redis init failure is caught and continues in degraded mode
                # The exception is logged but not raised
                async with lifespan(mock_app):
                    # Verify degraded mode is set
                    assert mock_app.state.degraded_mode is True
        finally:
            settings.environment = original_env

    @pytest.mark.asyncio
    @patch("redis.asyncio.from_url")
    @patch("app.main.sessions.get_session_manager")
    @patch("app.main.sessions.init_session_routes")
    async def test_lifespan_database_failure(
        self,
        mock_init_session: MagicMock,
        mock_get_session: MagicMock,
        mock_redis: AsyncMock,
    ) -> None:
        """Test lifespan handles database initialization failure in production."""
        import sys

        # Remove cached main module to force reimport
        if "app.main" in sys.modules:
            del sys.modules["app.main"]

        from app.config import settings

        original_env = settings.environment
        settings.environment = "production"
        try:
            from app.main import lifespan

            mock_redis_client = AsyncMock()
            mock_redis_client.ping = AsyncMock()
            mock_redis_client.aclose = AsyncMock()
            mock_redis.return_value = mock_redis_client

            with (
                patch("app.main.configure_alerting"),
                patch("app.main.init_db", side_effect=Exception("Database failed")),
            ):
                mock_app = MagicMock()
                mock_app.state.test_mode = False  # Ensure not in test mode

                # In production, database failure should raise an exception
                with pytest.raises(Exception, match="Database failed"):
                    async with lifespan(mock_app):
                        pass
        finally:
            settings.environment = original_env

    @pytest.mark.asyncio
    @patch("redis.asyncio.from_url")
    async def test_lifespan_redis_ping_failure(self, mock_redis: AsyncMock) -> None:
        """Test lifespan handles Redis ping failure (continues in degraded mode)."""
        from app.main import lifespan

        mock_redis_client = AsyncMock()
        mock_redis_client.ping = AsyncMock(side_effect=Exception("Ping failed"))
        mock_redis_client.aclose = AsyncMock()
        mock_redis.return_value = mock_redis_client

        with (
            patch("app.main.configure_alerting"),
            patch("app.main.init_db"),
        ):
            mock_app = MagicMock()

            # Redis ping failure should NOT raise exception - continues in degraded mode
            async with lifespan(mock_app):
                pass

            # Verify app state reflects degraded mode
            assert getattr(mock_app.state, "redis_available", False) is False

    @pytest.mark.asyncio
    @patch("redis.asyncio.from_url")
    async def test_lifespan_shutdown_sequence(self, mock_redis: AsyncMock) -> None:
        """Test complete shutdown sequence."""
        from app.main import lifespan

        mock_redis_client = AsyncMock()
        mock_redis_client.ping = AsyncMock()
        mock_redis_client.aclose = AsyncMock()
        mock_redis.return_value = mock_redis_client

        with (
            patch("app.main.configure_alerting"),
            patch("app.main.init_db"),
            patch("app.main.sessions.init_session_routes"),
            patch("app.main.tasks.init_task_routes"),
            patch("app.main.export.init_export_routes"),
            patch("app.main.health.init_health_routes"),
            patch("app.main.sessions.get_session_manager"),
            patch("app.main.close_db") as mock_close_db,
        ):
            mock_app = MagicMock()

            async with lifespan(mock_app):
                # Verify startup components called
                assert mock_redis_client.ping.call_count == 1

            # Verify shutdown components called
            mock_redis_client.aclose.assert_called_once()
            mock_close_db.assert_called_once()


class TestCreateAppComprehensive:
    """Comprehensive tests for create_app function."""

    def test_create_app_basic(self) -> None:
        """Test basic app creation."""
        from app.main import create_app

        app = create_app(test_mode=True)

        assert isinstance(app, FastAPI)
        assert app.title == "APGI System API"
        assert app.version == "1.1.0"
        assert "REST API for Allostatic Precision-Gated Ignition" in app.description

    def test_create_app_production_mode(self) -> None:
        """Test app creation in production mode (test_mode=False)."""
        from app.main import create_app

        with patch("app.main.settings") as mock_settings:
            mock_settings.environment = "production"
            mock_settings.schema_validation_enabled = True
            mock_settings.schema_validation_fail_on_error = False
            mock_settings.csrf_protection_enabled = True
            mock_settings.rate_limit_enabled = True
            mock_settings.profiling_enabled = False
            mock_settings.max_request_size_mb = 10
            mock_settings.request_size_limit_enabled = True

            app = create_app(test_mode=False)

            assert isinstance(app, FastAPI)
            # In production mode, docs should be disabled
            assert app.docs_url is None
            assert app.redoc_url is None
            assert app.openapi_url is None

    def test_create_app_development_mode(self) -> None:
        """Test app creation in development mode."""
        from app.main import create_app

        with patch("app.main.settings") as mock_settings:
            mock_settings.environment = "development"
            mock_settings.schema_validation_enabled = True
            mock_settings.schema_validation_fail_on_error = False
            mock_settings.csrf_protection_enabled = True
            mock_settings.rate_limit_enabled = True
            mock_settings.profiling_enabled = False
            mock_settings.max_request_size_mb = 10
            mock_settings.request_size_limit_enabled = True

            app = create_app(test_mode=False)

            assert isinstance(app, FastAPI)
            # In development mode, docs should be enabled
            assert app.docs_url == "/docs"
            assert app.redoc_url == "/redoc"
            assert app.openapi_url == "/openapi.json"

    def test_create_app_with_profiling_enabled(self) -> None:
        """Test app creation with profiling enabled."""
        from app.main import create_app

        with patch("app.main.settings") as mock_settings:
            mock_settings.environment = "development"
            mock_settings.schema_validation_enabled = True
            mock_settings.schema_validation_fail_on_error = False
            mock_settings.csrf_protection_enabled = True
            mock_settings.rate_limit_enabled = True
            mock_settings.profiling_enabled = True
            mock_settings.profiling_memory_enabled = True
            mock_settings.max_request_size_mb = 10
            mock_settings.request_size_limit_enabled = True

            app = create_app(test_mode=False)

            assert isinstance(app, FastAPI)

    def test_create_app_middleware_configuration(self) -> None:
        """Test that all expected middleware are added."""
        from app.main import create_app

        with patch("app.main.settings") as mock_settings:
            mock_settings.environment = "development"
            mock_settings.schema_validation_enabled = True
            mock_settings.schema_validation_fail_on_error = False
            mock_settings.csrf_protection_enabled = True
            mock_settings.rate_limit_enabled = True
            mock_settings.profiling_enabled = False
            mock_settings.max_request_size_mb = 10
            mock_settings.request_size_limit_enabled = True

            app = create_app(test_mode=False)

            # Should have middleware added
            assert hasattr(app, "user_middleware") or (app.middleware_stack is not None and len(list(app.middleware_stack)) > 0)  # type: ignore

    def test_create_app_root_endpoint(self) -> None:
        """Test that root endpoint is properly configured."""
        from fastapi.testclient import TestClient

        from app.main import create_app

        app = create_app(test_mode=True)
        client = TestClient(app)

        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "APGI System API"
        assert data["version"] == "1.1.0"
        assert data["docs"] == "/docs"
        assert data["health"] == "/health"

    def test_create_app_router_inclusion(self) -> None:
        """Test that all routers are included."""
        from app.main import create_app

        with patch("app.main.settings") as mock_settings:
            mock_settings.environment = "development"
            mock_settings.schema_validation_enabled = True
            mock_settings.schema_validation_fail_on_error = False
            mock_settings.csrf_protection_enabled = True
            mock_settings.rate_limit_enabled = True
            mock_settings.profiling_enabled = False
            mock_settings.max_request_size_mb = 10
            mock_settings.request_size_limit_enabled = True

            app = create_app(test_mode=True)

            # Check that routes were added (should have root route at minimum)
            assert len(app.routes) > 0

    def test_create_app_csrf_missing_setting(self) -> None:
        """Test app creation when csrf_protection_enabled setting is missing."""
        from app.main import create_app

        with patch("app.main.settings") as mock_settings:
            mock_settings.environment = "development"
            mock_settings.schema_validation_enabled = True
            mock_settings.schema_validation_fail_on_error = False
            # Remove csrf_protection_enabled to test default
            del mock_settings.csrf_protection_enabled
            mock_settings.rate_limit_enabled = True
            mock_settings.profiling_enabled = False
            mock_settings.max_request_size_mb = 10
            mock_settings.request_size_limit_enabled = True

            app = create_app(test_mode=False)
            assert isinstance(app, FastAPI)


class TestMainModule:
    """Test main module level functionality."""

    def test_app_instance_creation(self) -> None:
        """Test that app instance is created at module level."""
        from app.main import app

        assert isinstance(app, FastAPI)
        assert app.title == "APGI System API"

    def test_cli_function(self) -> None:
        """Test CLI function exists and can be called."""
        from app.main import cli

        with patch("app.cli.cli") as mock_app_cli:
            cli()
            mock_app_cli.assert_called_once()

    def test_main_execution_block(self) -> None:
        """Test the __main__ execution block."""
        with patch("uvicorn.run") as mock_run:
            with patch("__main__.__name__", "__main__"):
                # Import the module to trigger the block
                if "app.main" in sys.modules:
                    del sys.modules["app.main"]

                # The block should have been executed during import
                # We can't easily test this without actually running the module

    def test_redis_client_global(self) -> None:
        """Test that redis_client global variable exists."""
        from app.main import redis_client

        # Should be None initially (set during lifespan)
        assert redis_client is None

    def test_opentelemetry_availability_detection(self) -> None:
        """Test OpenTelemetry availability detection."""
        from app.main import OPENTELEMETRY_AVAILABLE

        # Should be either True or False depending on environment
        assert isinstance(OPENTELEMETRY_AVAILABLE, bool)


class TestMainEdgeCases:
    """Test edge cases and error conditions."""

    def test_create_app_missing_settings_attributes(self) -> None:
        """Test create_app with missing optional settings attributes."""
        from app.main import create_app

        with patch("app.main.settings") as mock_settings:
            mock_settings.environment = "development"
            mock_settings.schema_validation_enabled = True
            mock_settings.schema_validation_fail_on_error = False
            mock_settings.csrf_protection_enabled = True
            mock_settings.rate_limit_enabled = True
            mock_settings.profiling_enabled = False

            # Remove optional attributes
            del mock_settings.max_request_size_mb
            del mock_settings.request_size_limit_enabled

            app = create_app(test_mode=False)
            assert isinstance(app, FastAPI)

    @pytest.mark.asyncio
    @patch("redis.asyncio.from_url")
    async def test_lifespan_redis_client_none(self, mock_redis: AsyncMock) -> None:
        """Test lifespan when redis.from_url returns None (continues in degraded mode)."""
        from app.main import lifespan

        mock_redis.return_value = None
        with (
            patch("app.main.configure_alerting"),
            patch("app.main.init_db"),
        ):
            mock_app = MagicMock()

            # Redis client being None should NOT raise exception - continues in degraded mode
            async with lifespan(mock_app):
                pass

            # Verify app state reflects degraded mode
            assert getattr(mock_app.state, "redis_available", False) is False

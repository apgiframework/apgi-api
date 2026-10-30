"""
Tests for version information routes.
"""

import os
from unittest.mock import patch

import pytest
from fastapi.responses import JSONResponse

from app.routes.version import (
    API_VERSION,
    CURRENT_VERSION,
    DEPRECATED_ENDPOINTS,
    DEPRECATED_VERSIONS,
    SUPPORTED_VERSIONS,
    configure_deprecated_endpoints,
    get_client_documentation,
    get_deprecated_endpoints,
    get_version_info,
    is_endpoint_deprecated,
    router,
)


class TestVersionRoutes:
    """Test version information endpoints."""

    def test_router_configuration(self):
        """Test router is properly configured."""
        assert router.tags == ["Version"]
        assert router.prefix == "/v1"
        assert len(router.routes) > 0

    @pytest.mark.asyncio
    async def test_get_version_info(self):
        """Test get_version_info returns version information."""
        response = await get_version_info()
        assert response.status_code == 200

        content = response.body.decode()
        assert CURRENT_VERSION in content
        assert API_VERSION in content
        assert "/openapi.json" in content
        assert "/docs" in content

    @pytest.mark.asyncio
    async def test_get_version_info_timestamp(self):
        """Test version info includes timestamp."""
        response = await get_version_info()
        content = response.body.decode()
        assert "timestamp" in content

    @pytest.mark.asyncio
    async def test_get_client_documentation_python(self):
        """Test client documentation for Python."""
        response = await get_client_documentation(language="python")
        assert "language" in response
        assert response["language"] == "python"
        assert "base_url" in response
        assert "documentation" in response
        assert "endpoints" in response
        assert "import requests" in response["documentation"]["client_examples"]

    @pytest.mark.asyncio
    async def test_get_client_documentation_javascript(self):
        """Test client documentation for JavaScript."""
        response = await get_client_documentation(language="javascript")
        assert response["language"] == "javascript"
        assert "fetch" in response["documentation"]["client_examples"]

    @pytest.mark.asyncio
    async def test_get_client_documentation_curl(self):
        """Test client documentation for cURL."""
        response = await get_client_documentation(language="curl")
        assert response["language"] == "curl"
        assert "curl" in response["documentation"]["client_examples"]

    @pytest.mark.asyncio
    async def test_get_client_documentation_go(self):
        """Test client documentation for Go."""
        response = await get_client_documentation(language="go")
        assert response["language"] == "go"
        assert "package main" in response["documentation"]["client_examples"]

    @pytest.mark.asyncio
    async def test_get_client_documentation_unsupported_language(self):
        """Test client documentation with unsupported language."""
        response = await get_client_documentation(language="rust")
        assert isinstance(response, JSONResponse)
        assert response.status_code == 400
        content = response.body.decode()
        assert "Unsupported language" in content
        assert "rust" in content

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"API_BASE_URL": "https://api.example.com"})
    async def test_get_client_documentation_custom_base_url(self):
        """Test client documentation with custom base URL."""
        response = await get_client_documentation(language="python")
        assert "https://api.example.com" in response["base_url"]

    @pytest.mark.asyncio
    @patch.dict(os.environ, {"API_BASE_URL": "invalid-url"}, clear=True)
    async def test_get_client_documentation_invalid_base_url(self):
        """Test client documentation falls back to the canonical API URL."""
        response = await get_client_documentation(language="python")
        assert response["base_url"] == "https://api.apgiframework.com"

    def test_configure_deprecated_endpoints(self):
        """Test configuring deprecated endpoints."""
        # Save original state
        original = DEPRECATED_ENDPOINTS.copy()

        try:
            config = {
                "/v1/old-endpoint": {"sunset": "2026-01-01", "replacement": "/v2/new-endpoint"}
            }

            configure_deprecated_endpoints(config)

            # Re-import to get updated global
            from app.routes.version import DEPRECATED_ENDPOINTS as updated

            assert updated == config
        finally:
            # Restore original state
            from app.routes.version import DEPRECATED_ENDPOINTS as to_restore

            to_restore.clear()
            to_restore.update(original)

    def test_is_endpoint_deprecated(self):
        """Test checking if endpoint is deprecated."""
        # Save original state
        original = DEPRECATED_ENDPOINTS.copy()

        try:
            DEPRECATED_ENDPOINTS["/v1/test"] = {"sunset": "2026-01-01"}

            result = is_endpoint_deprecated("/v1/test")
            assert result == {"sunset": "2026-01-01"}

            result = is_endpoint_deprecated("/v1/other")
            assert result is None
        finally:
            # Restore original state
            from app.routes.version import DEPRECATED_ENDPOINTS as to_restore

            to_restore.clear()
            to_restore.update(original)

    def test_get_deprecated_endpoints(self):
        """Test getting all deprecated endpoints."""
        # Save original state
        original = DEPRECATED_ENDPOINTS.copy()

        try:
            DEPRECATED_ENDPOINTS["/v1/test"] = {"sunset": "2026-01-01"}

            result = get_deprecated_endpoints()
            assert result == DEPRECATED_ENDPOINTS
        finally:
            # Restore original state
            from app.routes.version import DEPRECATED_ENDPOINTS as to_restore

            to_restore.clear()
            to_restore.update(original)

    def test_version_constants(self):
        """Test version constants are properly set."""
        assert isinstance(CURRENT_VERSION, str)
        assert isinstance(API_VERSION, str)
        assert isinstance(SUPPORTED_VERSIONS, list)
        isinstance(DEPRECATED_VERSIONS, list)
        assert isinstance(DEPRECATED_ENDPOINTS, dict)

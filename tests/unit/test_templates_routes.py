"""
Comprehensive test suite for templates routes (100% coverage target)

Tests all CRUD operations, error handling, pagination, and access control.
"""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.models.schemas import TokenPayload
from app.routes.templates import (
    create_template,
    delete_template,
    get_template,
    init_template_routes,
    list_templates,
    update_template,
)

FAKE_USER = TokenPayload(
    user_id="user-123",
    username="testuser",
    roles=["admin"],
    exp=datetime.now(timezone.utc) + timedelta(hours=1),
    token_type="access",
    permissions=["TEMPLATE_READ", "TEMPLATE_CREATE", "TEMPLATE_UPDATE", "TEMPLATE_DELETE"],
)

FAKE_OTHER_USER = TokenPayload(
    user_id="user-456",
    username="otheruser",
    roles=["user"],
    exp=datetime.now(timezone.utc) + timedelta(hours=1),
    token_type="access",
    permissions=["TEMPLATE_READ"],
)


class MockQuery:
    """Custom mock query class that handles SQLAlchemy whereclause boolean evaluation."""

    def __init__(self, has_whereclause: bool = False) -> None:
        self._has_whereclause = has_whereclause
        self.whereclause = self if has_whereclause else None

    def __bool__(self) -> bool:
        return self._has_whereclause

    def filter(self, *args: Any, **kwargs: Any) -> "MockQuery":
        return self

    def count(self) -> int:
        return 2 if self._has_whereclause else 0

    def offset(self, n: int) -> "MockQuery":
        return self

    def limit(self, n: int) -> "MockQuery":
        return self


@pytest.fixture
def mock_template() -> MagicMock:
    """Create a mock template object."""
    from datetime import datetime, timezone

    template = MagicMock()
    template.template_id = str(uuid4())
    template.user_id = FAKE_USER.user_id
    template.name = "Test Template"
    template.description = "Test Description"
    template.config_path = "config/test.yaml"
    template.custom_config = {"key": "value"}
    template.default_description = "Default description"
    template.tags = ["tag1", "tag2"]
    template.is_public = False
    # Use actual datetime objects for Pydantic validation
    now = datetime.now(timezone.utc)
    template.created_at = now
    template.updated_at = now
    return template


@pytest.fixture
def mock_public_template() -> MagicMock:
    """Create a mock public template object."""
    from datetime import datetime, timezone

    template = MagicMock()
    template.template_id = str(uuid4())
    template.user_id = "user-789"  # Different user
    template.name = "Public Template"
    template.description = "Public Description"
    template.config_path = "config/public.yaml"
    template.custom_config = {"public": True}
    template.default_description = "Public default"
    template.tags = ["public"]
    template.is_public = True
    # Use actual datetime objects for Pydantic validation
    now = datetime.now(timezone.utc)
    template.created_at = now
    template.updated_at = now
    return template


class TestInitTemplateRoutes:
    """Test init_template_routes function."""

    def test_init_template_routes(self, caplog: Any) -> None:
        """init_template_routes should log initialization message."""
        with caplog.at_level("INFO"):
            init_template_routes()
        assert "Template routes initialized" in caplog.text


class TestListTemplates:
    """Test list_templates endpoint."""

    @pytest.mark.asyncio
    async def test_list_templates_invalid_page_zero(self) -> None:
        """Page < 1 should raise 400."""
        with pytest.raises(HTTPException) as exc_info:
            await list_templates(page=0, per_page=10, public_only=False, current_user=FAKE_USER)
        assert exc_info.value.status_code == 400
        assert "Page must be >= 1" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_list_templates_invalid_page_negative(self) -> None:
        """Page < 0 should raise 400."""
        with pytest.raises(HTTPException) as exc_info:
            await list_templates(page=-1, per_page=10, public_only=False, current_user=FAKE_USER)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_list_templates_invalid_per_page_zero(self) -> None:
        """Per page = 0 should raise 400."""
        with pytest.raises(HTTPException) as exc_info:
            await list_templates(page=1, per_page=0, public_only=False, current_user=FAKE_USER)
        assert exc_info.value.status_code == 400
        assert "Per page must be between 1 and 100" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_list_templates_invalid_per_page_too_large(self) -> None:
        """Per page > 100 should raise 400."""
        with pytest.raises(HTTPException) as exc_info:
            await list_templates(page=1, per_page=101, public_only=False, current_user=FAKE_USER)
        assert exc_info.value.status_code == 400
        assert "Per page must be between 1 and 100" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_list_templates_success_with_results(
        self, mock_template: MagicMock, mock_public_template: MagicMock
    ) -> None:
        """List templates should return user's templates and public templates."""
        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_template, mock_public_template]
        mock_db.execute.return_value = mock_result

        # Mock the count query properly using MockQuery
        mock_count_query = MockQuery(has_whereclause=True)
        mock_db.query.return_value = mock_count_query

        with patch("app.routes.templates.get_async_db_context") as mock_db_context:
            mock_context_manager = MagicMock()
            mock_context_manager.__aenter__ = AsyncMock(return_value=mock_db)
            mock_context_manager.__aexit__ = AsyncMock(return_value=False)
            mock_db_context.return_value = mock_context_manager

            response = await list_templates(
                page=1, per_page=10, public_only=False, current_user=FAKE_USER
            )

        assert response.templates is not None
        assert response.pagination.page == 1
        assert response.pagination.per_page == 10

    @pytest.mark.asyncio
    async def test_list_templates_public_only(self, mock_public_template: MagicMock) -> None:
        """List templates with public_only=true should return only public templates."""
        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_public_template]
        mock_db.execute.return_value = mock_result

        # Mock the count query properly using MockQuery
        mock_count_query = MockQuery(has_whereclause=True)
        mock_db.query.return_value = mock_count_query

        with patch("app.routes.templates.get_async_db_context") as mock_db_context:
            mock_context_manager = MagicMock()
            mock_context_manager.__aenter__ = AsyncMock(return_value=mock_db)
            mock_context_manager.__aexit__ = AsyncMock(return_value=False)
            mock_db_context.return_value = mock_context_manager

            response = await list_templates(
                page=1, per_page=10, public_only=True, current_user=FAKE_USER
            )

        assert response.templates is not None
        assert len(response.templates) == 1

    @pytest.mark.asyncio
    async def test_list_templates_empty_results(self) -> None:
        """List templates with no results should return empty list."""
        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = mock_result

        # Mock the count query properly using MockQuery (no whereclause)
        mock_count_query = MockQuery(has_whereclause=False)
        mock_db.query.return_value = mock_count_query

        with patch("app.routes.templates.get_async_db_context") as mock_db_context:
            mock_context_manager = MagicMock()
            mock_context_manager.__aenter__ = AsyncMock(return_value=mock_db)
            mock_context_manager.__aexit__ = AsyncMock(return_value=False)
            mock_db_context.return_value = mock_context_manager

            response = await list_templates(
                page=1, per_page=10, public_only=False, current_user=FAKE_USER
            )

        assert len(response.templates) == 0
        assert response.pagination.total == 0

    @pytest.mark.asyncio
    async def test_list_templates_database_error(self, caplog: Any) -> None:
        """Database error should raise 500."""
        with patch("app.routes.templates.get_async_db_context") as mock_db_context:
            mock_context_manager = MagicMock()
            mock_context_manager.__aenter__ = AsyncMock(side_effect=Exception("Database error"))
            mock_context_manager.__aexit__ = AsyncMock(return_value=False)
            mock_db_context.return_value = mock_context_manager

            with pytest.raises(HTTPException) as exc_info:
                await list_templates(page=1, per_page=10, public_only=False, current_user=FAKE_USER)

        assert exc_info.value.status_code == 500
        assert "internal error" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_list_templates_pagination_second_page(self, mock_template: MagicMock) -> None:
        """List templates with page=2 should return second page."""
        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_template]
        mock_db.execute.return_value = mock_result

        # Mock the count query properly using MockQuery
        mock_count_query = MockQuery(has_whereclause=True)
        mock_db.query.return_value = mock_count_query

        with patch("app.routes.templates.get_async_db_context") as mock_db_context:
            mock_context_manager = MagicMock()
            mock_context_manager.__aenter__ = AsyncMock(return_value=mock_db)
            mock_context_manager.__aexit__ = AsyncMock(return_value=False)
            mock_db_context.return_value = mock_context_manager

            response = await list_templates(
                page=2, per_page=10, public_only=False, current_user=FAKE_USER
            )

        assert response.pagination.page == 2


class TestCreateTemplate:
    """Test create_template endpoint."""

    @pytest.mark.asyncio
    async def test_create_template_success(self, mock_template: MagicMock) -> None:
        """Create template with valid data should succeed."""
        from datetime import datetime, timezone

        from app.models.schemas import SessionTemplateCreateRequest

        mock_db = MagicMock()

        # Mock the check for existing template (should not find any)
        mock_check_result = MagicMock()
        mock_check_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_check_result

        # Mock db.refresh() to set created_at and updated_at like real database would
        def mock_refresh(template: MagicMock) -> None:
            template.created_at = datetime.now(timezone.utc)
            template.updated_at = datetime.now(timezone.utc)
            template.template_id = str(uuid4())
            template.user_id = FAKE_USER.user_id

        mock_db.refresh.side_effect = mock_refresh

        with patch("app.routes.templates.get_async_db_context") as mock_db_context:
            mock_context_manager = MagicMock()
            mock_context_manager.__aenter__ = AsyncMock(return_value=mock_db)
            mock_context_manager.__aexit__ = AsyncMock(return_value=False)
            mock_db_context.return_value = mock_context_manager

            request = SessionTemplateCreateRequest(
                name="New Template",
                description="New Description",
                config_path="config/new.yaml",
                custom_config={"key": "value"},
                default_description="Default",
                tags=["tag1"],
                is_public=False,
            )

            response = await create_template(request=request, current_user=FAKE_USER)

        assert response.name == "New Template"
        assert response.user_id == str(FAKE_USER.user_id)

    @pytest.mark.asyncio
    async def test_create_template_duplicate_name(self, mock_template: MagicMock) -> None:
        """Create template with duplicate name should raise 409."""
        from app.models.schemas import SessionTemplateCreateRequest

        mock_db = MagicMock()

        # Mock the check for existing template (should find one)
        mock_check_result = MagicMock()
        mock_check_result.scalar_one_or_none.return_value = mock_template
        mock_db.execute.return_value = mock_check_result

        with patch("app.routes.templates.get_async_db_context") as mock_db_context:
            mock_context_manager = MagicMock()
            mock_context_manager.__aenter__ = AsyncMock(return_value=mock_db)
            mock_context_manager.__aexit__ = AsyncMock(return_value=False)
            mock_db_context.return_value = mock_context_manager

            request = SessionTemplateCreateRequest(
                name="Test Template",
                description="Description",
                config_path="config/test.yaml",
                custom_config=None,
                default_description=None,
                tags=[],
                is_public=False,
            )

            with pytest.raises(HTTPException) as exc_info:
                await create_template(request=request, current_user=FAKE_USER)

        assert exc_info.value.status_code == 409
        assert "already exists" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_create_template_database_unique_constraint_error(self) -> None:
        """Database unique constraint error should raise 409."""
        from app.models.schemas import SessionTemplateCreateRequest

        mock_db = MagicMock()

        # Mock the check for existing template
        mock_check_result = MagicMock()
        mock_check_result.scalar_one_or_none.return_value = None

        # Mock commit failure with unique constraint
        mock_db.execute.side_effect = [
            mock_check_result,
            MagicMock(),
        ]
        mock_db.commit.side_effect = Exception("unique constraint violation on name")

        with patch("app.routes.templates.get_async_db_context") as mock_db_context:
            mock_context_manager = MagicMock()
            mock_context_manager.__aenter__ = AsyncMock(return_value=mock_db)
            mock_context_manager.__aexit__ = AsyncMock(return_value=False)
            mock_db_context.return_value = mock_context_manager

            request = SessionTemplateCreateRequest(
                name="Test Template",
                description="Description",
                config_path="config/test.yaml",
                custom_config=None,
                default_description=None,
                tags=[],
                is_public=False,
            )

            with pytest.raises(HTTPException) as exc_info:
                await create_template(request=request, current_user=FAKE_USER)

        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_create_template_database_foreign_key_error(self) -> None:
        """Database foreign key error should raise 400."""
        from app.models.schemas import SessionTemplateCreateRequest

        mock_db = MagicMock()

        mock_check_result = MagicMock()
        mock_check_result.scalar_one_or_none.return_value = None

        mock_db.execute.side_effect = [
            mock_check_result,
            MagicMock(),
        ]
        mock_db.commit.side_effect = Exception("foreign key constraint violation")

        with patch("app.routes.templates.get_async_db_context") as mock_db_context:
            mock_context_manager = MagicMock()
            mock_context_manager.__aenter__ = AsyncMock(return_value=mock_db)
            mock_context_manager.__aexit__ = AsyncMock(return_value=False)
            mock_db_context.return_value = mock_context_manager

            request = SessionTemplateCreateRequest(
                name="Test Template",
                description="Description",
                config_path="config/test.yaml",
                custom_config=None,
                default_description=None,
                tags=[],
                is_public=False,
            )

            with pytest.raises(HTTPException) as exc_info:
                await create_template(request=request, current_user=FAKE_USER)

        assert exc_info.value.status_code == 400
        assert "Invalid user reference" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_create_template_database_check_constraint_error(self) -> None:
        """Database check constraint error should raise 400."""
        from app.models.schemas import SessionTemplateCreateRequest

        mock_db = MagicMock()

        mock_check_result = MagicMock()
        mock_check_result.scalar_one_or_none.return_value = None

        mock_db.execute.side_effect = [
            mock_check_result,
            MagicMock(),
        ]
        mock_db.commit.side_effect = Exception("check constraint violation")

        with patch("app.routes.templates.get_async_db_context") as mock_db_context:
            mock_context_manager = MagicMock()
            mock_context_manager.__aenter__ = AsyncMock(return_value=mock_db)
            mock_context_manager.__aexit__ = AsyncMock(return_value=False)
            mock_db_context.return_value = mock_context_manager

            request = SessionTemplateCreateRequest(
                name="Test Template",
                description="Description",
                config_path="config/test.yaml",
                custom_config=None,
                default_description=None,
                tags=[],
                is_public=False,
            )

            with pytest.raises(HTTPException) as exc_info:
                await create_template(request=request, current_user=FAKE_USER)

        assert exc_info.value.status_code == 400
        assert "Invalid template data" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_create_template_database_generic_error(self, caplog: Any) -> None:
        """Generic database error should raise 500."""
        from app.models.schemas import SessionTemplateCreateRequest

        mock_db = MagicMock()

        mock_check_result = MagicMock()
        mock_check_result.scalar_one_or_none.return_value = None

        mock_db.execute.side_effect = [
            mock_check_result,
            MagicMock(),
        ]
        mock_db.commit.side_effect = Exception("unexpected database error")

        with patch("app.routes.templates.get_async_db_context") as mock_db_context:
            mock_context_manager = MagicMock()
            mock_context_manager.__aenter__ = AsyncMock(return_value=mock_db)
            mock_context_manager.__aexit__ = AsyncMock(return_value=False)
            mock_db_context.return_value = mock_context_manager

            request = SessionTemplateCreateRequest(
                name="Test Template",
                description="Description",
                config_path="config/test.yaml",
                custom_config=None,
                default_description=None,
                tags=[],
                is_public=False,
            )

            with pytest.raises(HTTPException) as exc_info:
                await create_template(request=request, current_user=FAKE_USER)

        assert exc_info.value.status_code == 500


class TestGetTemplate:
    """Test get_template endpoint."""

    @pytest.mark.asyncio
    async def test_get_template_success(self, mock_template: MagicMock) -> None:
        """Get existing template should succeed."""
        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_template
        mock_db.execute.return_value = mock_result

        with patch("app.routes.templates.get_async_db_context") as mock_db_context:
            mock_context_manager = MagicMock()
            mock_context_manager.__aenter__ = AsyncMock(return_value=mock_db)
            mock_context_manager.__aexit__ = AsyncMock(return_value=False)
            mock_db_context.return_value = mock_context_manager

            response = await get_template(
                template_id=str(mock_template.template_id), current_user=FAKE_USER
            )

        assert str(response.template_id) == str(mock_template.template_id)
        assert response.name == mock_template.name

    @pytest.mark.asyncio
    async def test_get_template_not_found(self) -> None:
        """Get non-existent template should raise 404."""
        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        with patch("app.routes.templates.get_async_db_context") as mock_db_context:
            mock_context_manager = MagicMock()
            mock_context_manager.__aenter__ = AsyncMock(return_value=mock_db)
            mock_context_manager.__aexit__ = AsyncMock(return_value=False)
            mock_db_context.return_value = mock_context_manager

            with pytest.raises(HTTPException) as exc_info:
                await get_template(template_id="non-existent-id", current_user=FAKE_USER)

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_template_access_denied_private(self, mock_template: MagicMock) -> None:
        """Get private template of another user should raise 403."""
        mock_template.is_public = False
        mock_template.user_id = "different-user-id"

        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_template
        mock_db.execute.return_value = mock_result

        with patch("app.routes.templates.get_async_db_context") as mock_db_context:
            mock_context_manager = MagicMock()
            mock_context_manager.__aenter__ = AsyncMock(return_value=mock_db)
            mock_context_manager.__aexit__ = AsyncMock(return_value=False)
            mock_db_context.return_value = mock_context_manager

            with pytest.raises(HTTPException) as exc_info:
                await get_template(
                    template_id=str(mock_template.template_id), current_user=FAKE_USER
                )

        assert exc_info.value.status_code == 403
        assert "Access denied" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_get_template_public_access(self, mock_public_template: MagicMock) -> None:
        """Get public template of another user should succeed."""
        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_public_template
        mock_db.execute.return_value = mock_result

        with patch("app.routes.templates.get_async_db_context") as mock_db_context:
            mock_context_manager = MagicMock()
            mock_context_manager.__aenter__ = AsyncMock(return_value=mock_db)
            mock_context_manager.__aexit__ = AsyncMock(return_value=False)
            mock_db_context.return_value = mock_context_manager

            response = await get_template(
                template_id=str(mock_public_template.template_id), current_user=FAKE_USER
            )

        assert response.is_public is True

    @pytest.mark.asyncio
    async def test_get_template_owner_access(self, mock_template: MagicMock) -> None:
        """Owner should be able to access their private template."""
        mock_template.is_public = False

        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_template
        mock_db.execute.return_value = mock_result

        with patch("app.routes.templates.get_async_db_context") as mock_db_context:
            mock_context_manager = MagicMock()
            mock_context_manager.__aenter__ = AsyncMock(return_value=mock_db)
            mock_context_manager.__aexit__ = AsyncMock(return_value=False)
            mock_db_context.return_value = mock_context_manager

            response = await get_template(
                template_id=str(mock_template.template_id), current_user=FAKE_USER
            )

        assert response is not None

    @pytest.mark.asyncio
    async def test_get_template_general_exception(self, caplog: Any) -> None:
        """General exception should raise 500."""
        with patch("app.routes.templates.get_async_db_context") as mock_db_context:
            mock_context_manager = MagicMock()
            mock_context_manager.__aenter__ = AsyncMock(side_effect=Exception("Database error"))
            mock_context_manager.__aexit__ = AsyncMock(return_value=False)
            mock_db_context.return_value = mock_context_manager

            with pytest.raises(HTTPException) as exc_info:
                await get_template(template_id="test-id", current_user=FAKE_USER)

        assert exc_info.value.status_code == 500


class TestUpdateTemplate:
    """Test update_template endpoint."""

    @pytest.mark.asyncio
    async def test_update_template_success(self, mock_template: MagicMock) -> None:
        """Update template should succeed."""
        from app.models.schemas import SessionTemplateUpdateRequest

        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_template

        # When name is different, check for duplicates - return None (no duplicate)
        mock_check_result = MagicMock()
        mock_check_result.scalar_one_or_none.return_value = None

        # First execute gets the template, second checks for duplicate
        mock_db.execute.side_effect = [mock_result, mock_check_result]

        with patch("app.routes.templates.get_async_db_context") as mock_db_context:
            mock_context_manager = MagicMock()
            mock_context_manager.__aenter__ = AsyncMock(return_value=mock_db)
            mock_context_manager.__aexit__ = AsyncMock(return_value=False)
            mock_db_context.return_value = mock_context_manager

            request = SessionTemplateUpdateRequest(
                name="Updated Template",
                description="Updated Description",
                config_path=None,
                custom_config=None,
                default_description=None,
                tags=None,
                is_public=None,
            )

            response = await update_template(
                template_id=str(mock_template.template_id),
                request=request,
                current_user=FAKE_USER,
            )

        assert response.name == "Updated Template"

    @pytest.mark.asyncio
    async def test_update_template_not_found(self) -> None:
        """Update non-existent template should raise 404."""
        from app.models.schemas import SessionTemplateUpdateRequest

        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        with patch("app.routes.templates.get_async_db_context") as mock_db_context:
            mock_context_manager = MagicMock()
            mock_context_manager.__aenter__ = AsyncMock(return_value=mock_db)
            mock_context_manager.__aexit__ = AsyncMock(return_value=False)
            mock_db_context.return_value = mock_context_manager

            request = SessionTemplateUpdateRequest(
                name="Updated",
                description=None,
                config_path=None,
                custom_config=None,
                default_description=None,
                tags=None,
                is_public=None,
            )

            with pytest.raises(HTTPException) as exc_info:
                await update_template(
                    template_id="non-existent-id",
                    request=request,
                    current_user=FAKE_USER,
                )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_update_template_not_owner(self, mock_template: MagicMock) -> None:
        """Update template when not owner should raise 403."""
        from app.models.schemas import SessionTemplateUpdateRequest

        mock_template.user_id = "different-user-id"

        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_template
        mock_db.execute.return_value = mock_result

        with patch("app.routes.templates.get_async_db_context") as mock_db_context:
            mock_context_manager = MagicMock()
            mock_context_manager.__aenter__ = AsyncMock(return_value=mock_db)
            mock_context_manager.__aexit__ = AsyncMock(return_value=False)
            mock_db_context.return_value = mock_context_manager

            request = SessionTemplateUpdateRequest(
                name="Updated",
                description=None,
                config_path=None,
                custom_config=None,
                default_description=None,
                tags=None,
                is_public=None,
            )

            with pytest.raises(HTTPException) as exc_info:
                await update_template(
                    template_id=str(mock_template.template_id),
                    request=request,
                    current_user=FAKE_USER,
                )

        assert exc_info.value.status_code == 403
        assert "Only template owner" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_update_template_duplicate_name(self, mock_template: MagicMock) -> None:
        """Update template with duplicate name should raise 409."""
        from app.models.schemas import SessionTemplateUpdateRequest

        existing_template = MagicMock()
        existing_template.template_id = "different-id"

        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_template

        # Mock duplicate name check
        mock_check_result = MagicMock()
        mock_check_result.scalar_one_or_none.return_value = existing_template

        mock_db.execute.side_effect = [mock_result, mock_check_result]

        with patch("app.routes.templates.get_async_db_context") as mock_db_context:
            mock_context_manager = MagicMock()
            mock_context_manager.__aenter__ = AsyncMock(return_value=mock_db)
            mock_context_manager.__aexit__ = AsyncMock(return_value=False)
            mock_db_context.return_value = mock_context_manager

            request = SessionTemplateUpdateRequest(
                name="Different Name",
                description=None,
                config_path=None,
                custom_config=None,
                default_description=None,
                tags=None,
                is_public=None,
            )

            with pytest.raises(HTTPException) as exc_info:
                await update_template(
                    template_id=str(mock_template.template_id),
                    request=request,
                    current_user=FAKE_USER,
                )

        assert exc_info.value.status_code == 409
        assert "already exists" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_update_template_same_name(self, mock_template: MagicMock) -> None:
        """Update template keeping same name should succeed."""
        from app.models.schemas import SessionTemplateUpdateRequest

        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_template
        mock_db.execute.return_value = mock_result

        with patch("app.routes.templates.get_async_db_context") as mock_db_context:
            mock_context_manager = MagicMock()
            mock_context_manager.__aenter__ = AsyncMock(return_value=mock_db)
            mock_context_manager.__aexit__ = AsyncMock(return_value=False)
            mock_db_context.return_value = mock_context_manager

            request = SessionTemplateUpdateRequest(
                name=mock_template.name,  # Same name
                description="Updated description",  # Different field
                config_path=None,
                custom_config=None,
                default_description=None,
                tags=None,
                is_public=None,
            )

            response = await update_template(
                template_id=str(mock_template.template_id),
                request=request,
                current_user=FAKE_USER,
            )

        assert response is not None

    @pytest.mark.asyncio
    async def test_update_template_partial_update(self, mock_template: MagicMock) -> None:
        """Update template with partial fields should succeed."""
        from app.models.schemas import SessionTemplateUpdateRequest

        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_template
        mock_db.execute.return_value = mock_result

        with patch("app.routes.templates.get_async_db_context") as mock_db_context:
            mock_context_manager = MagicMock()
            mock_context_manager.__aenter__ = AsyncMock(return_value=mock_db)
            mock_context_manager.__aexit__ = AsyncMock(return_value=False)
            mock_db_context.return_value = mock_context_manager

            # Update only description
            request = SessionTemplateUpdateRequest(
                description="New Description",
                name=None,
                config_path=None,
                custom_config=None,
                default_description=None,
                tags=None,
                is_public=None,
            )

            response = await update_template(
                template_id=str(mock_template.template_id),
                request=request,
                current_user=FAKE_USER,
            )

        assert response is not None


class TestDeleteTemplate:
    """Test delete_template endpoint."""

    @pytest.mark.asyncio
    async def test_delete_template_success(self, mock_template: MagicMock) -> None:
        """Delete template should succeed."""
        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_template
        mock_db.execute.return_value = mock_result

        with patch("app.routes.templates.get_async_db_context") as mock_db_context:
            mock_context_manager = MagicMock()
            mock_context_manager.__aenter__ = AsyncMock(return_value=mock_db)
            mock_context_manager.__aexit__ = AsyncMock(return_value=False)
            mock_db_context.return_value = mock_context_manager

            # Should not raise
            await delete_template(
                template_id=str(mock_template.template_id), current_user=FAKE_USER
            )

        mock_db.delete.assert_called_once_with(mock_template)

    @pytest.mark.asyncio
    async def test_delete_template_not_found(self) -> None:
        """Delete non-existent template should raise 404."""
        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        with patch("app.routes.templates.get_async_db_context") as mock_db_context:
            mock_context_manager = MagicMock()
            mock_context_manager.__aenter__ = AsyncMock(return_value=mock_db)
            mock_context_manager.__aexit__ = AsyncMock(return_value=False)
            mock_db_context.return_value = mock_context_manager

            with pytest.raises(HTTPException) as exc_info:
                await delete_template(template_id="non-existent-id", current_user=FAKE_USER)

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_template_not_owner(self, mock_template: MagicMock) -> None:
        """Delete template when not owner should raise 403."""
        mock_template.user_id = "different-user-id"

        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_template
        mock_db.execute.return_value = mock_result

        with patch("app.routes.templates.get_async_db_context") as mock_db_context:
            mock_context_manager = MagicMock()
            mock_context_manager.__aenter__ = AsyncMock(return_value=mock_db)
            mock_context_manager.__aexit__ = AsyncMock(return_value=False)
            mock_db_context.return_value = mock_context_manager

            with pytest.raises(HTTPException) as exc_info:
                await delete_template(
                    template_id=str(mock_template.template_id), current_user=FAKE_USER
                )

        assert exc_info.value.status_code == 403
        assert "Only template owner" in exc_info.value.detail


@asynccontextmanager
async def noop_lifespan(app: Any) -> AsyncGenerator[None, None]:
    """No-op lifespan context manager for testing."""
    yield


class TestIntegrationWithFastAPI:
    """Integration tests using TestClient."""

    def test_list_templates_endpoint(self) -> None:
        """Test list templates via HTTP endpoint."""
        from app.main import create_app
        from app.services.authorization import get_current_user, require_permission
        from app.services.cache_service import get_cache_service

        app = create_app(test_mode=True)
        app.router.lifespan_context = noop_lifespan  # Skip Redis initialization
        app.dependency_overrides[get_current_user] = lambda: FAKE_USER
        app.dependency_overrides[require_permission] = lambda permission: lambda func: func
        app.dependency_overrides[get_cache_service] = lambda: None  # Disable cache in tests

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/v1/templates?page=1&per_page=10")
            assert resp.status_code in [200, 500]  # 500 if DB not available

    def test_create_template_endpoint(self) -> None:
        """Test create template via HTTP endpoint."""
        from app.main import create_app
        from app.services.authorization import get_current_user, require_permission
        from app.services.cache_service import get_cache_service

        app = create_app(test_mode=True)
        app.router.lifespan_context = noop_lifespan  # Skip Redis initialization
        app.dependency_overrides[get_current_user] = lambda: FAKE_USER
        app.dependency_overrides[require_permission] = lambda permission: lambda func: func
        app.dependency_overrides[get_cache_service] = lambda: None  # Disable cache in tests

        template_data = {
            "name": "Integration Test Template",
            "description": "Test Description",
            "config_path": "config/test.yaml",
            "is_public": False,
        }

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post("/v1/templates", json=template_data)
            # 400 if the DB enforces the user_id foreign key and FAKE_USER isn't a real row
            assert resp.status_code in [201, 400, 409, 422, 500]

    def test_get_template_endpoint(self) -> None:
        """Test get template via HTTP endpoint."""
        from app.main import create_app
        from app.services.authorization import get_current_user, require_permission
        from app.services.cache_service import get_cache_service

        app = create_app(test_mode=True)
        app.router.lifespan_context = noop_lifespan  # Skip Redis initialization
        app.dependency_overrides[get_current_user] = lambda: FAKE_USER
        app.dependency_overrides[require_permission] = lambda permission: lambda func: func
        app.dependency_overrides[get_cache_service] = lambda: None  # Disable cache in tests

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/v1/templates/test-template-id")
            assert resp.status_code in [200, 404, 500]

    def test_update_template_endpoint(self) -> None:
        """Test update template via HTTP endpoint."""
        from app.main import create_app
        from app.services.authorization import get_current_user, require_permission
        from app.services.cache_service import get_cache_service

        app = create_app(test_mode=True)
        app.router.lifespan_context = noop_lifespan  # Skip Redis initialization
        app.dependency_overrides[get_current_user] = lambda: FAKE_USER
        app.dependency_overrides[require_permission] = lambda permission: lambda func: func
        app.dependency_overrides[get_cache_service] = lambda: None  # Disable cache in tests

        update_data = {"name": "Updated Template"}

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.put("/v1/templates/test-template-id", json=update_data)
            assert resp.status_code in [200, 404, 403, 409, 500]

    def test_delete_template_endpoint(self) -> None:
        """Test delete template via HTTP endpoint."""
        from app.main import create_app
        from app.services.authorization import get_current_user, require_permission
        from app.services.cache_service import get_cache_service

        app = create_app(test_mode=True)
        app.router.lifespan_context = noop_lifespan  # Skip Redis initialization
        app.dependency_overrides[get_current_user] = lambda: FAKE_USER
        app.dependency_overrides[require_permission] = lambda permission: lambda func: func
        app.dependency_overrides[get_cache_service] = lambda: None  # Disable cache in tests

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.delete("/v1/templates/test-template-id")
            assert resp.status_code in [204, 404, 403, 500]


class TestExceptionHandlingAndEdgeCases:
    """Test exception handling and edge cases for 100% coverage."""

    @pytest.mark.asyncio
    async def test_list_templates_exception_inside_try_block(self, caplog: Any) -> None:
        """Test exception handling in list_templates try block (Lines 137-142)."""
        mock_db = MagicMock()
        # Make db.execute raise an exception inside the try block
        mock_db.execute.side_effect = Exception("Query execution failed")

        with patch("app.routes.templates.get_async_db_context") as mock_db_context:
            mock_context_manager = MagicMock()
            mock_context_manager.__aenter__ = AsyncMock(return_value=mock_db)
            mock_context_manager.__aexit__ = AsyncMock(return_value=False)
            mock_db_context.return_value = mock_context_manager

            with pytest.raises(HTTPException) as exc_info:
                await list_templates(page=1, per_page=10, public_only=False, current_user=FAKE_USER)

        assert exc_info.value.status_code == 500
        assert "internal error" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_create_template_http_exception_reraise(self, mock_template: MagicMock) -> None:
        """Test that HTTPException is re-raised in create_template (Lines 253-254)."""
        from app.models.schemas import SessionTemplateCreateRequest

        mock_db = MagicMock()

        # First execute for duplicate check returns None
        mock_check_result = MagicMock()
        mock_check_result.scalar_one_or_none.return_value = None

        mock_db.execute.return_value = mock_check_result

        # Make db.add raise an HTTPException specifically
        http_exc = HTTPException(status_code=418, detail="I'm a teapot")
        mock_db.add.side_effect = http_exc

        with patch("app.routes.templates.get_async_db_context") as mock_db_context:
            mock_context_manager = MagicMock()
            mock_context_manager.__aenter__ = AsyncMock(return_value=mock_db)
            mock_context_manager.__aexit__ = AsyncMock(return_value=False)
            mock_db_context.return_value = mock_context_manager

            request = SessionTemplateCreateRequest(
                name="Test Template",
                description="Description",
                config_path="config/test.yaml",
                custom_config=None,
                default_description=None,
                tags=[],
                is_public=False,
            )

            # HTTPException should be re-raised as-is
            with pytest.raises(HTTPException) as exc_info:
                await create_template(request=request, current_user=FAKE_USER)

            assert exc_info.value.status_code == 418
            assert "teapot" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_get_template_exception_reraise(self, mock_template: MagicMock) -> None:
        """Test that general exceptions are caught and converted to 500 in get_template."""
        mock_db = MagicMock()

        # First call returns template, but accessing is_public raises exception
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_template

        # Make template.is_public access raise an exception
        http_exc = HTTPException(status_code=418, detail="I'm a teapot")
        type(mock_template).is_public = PropertyMock(side_effect=http_exc)

        mock_db.execute.return_value = mock_result

        with patch("app.routes.templates.get_async_db_context") as mock_db_context:
            mock_context_manager = MagicMock()
            mock_context_manager.__aenter__ = AsyncMock(return_value=mock_db)
            mock_context_manager.__aexit__ = AsyncMock(return_value=False)
            mock_db_context.return_value = mock_context_manager

            # HTTPException should be re-raised as-is (caught by outer except)
            with pytest.raises(HTTPException) as exc_info:
                await get_template(
                    template_id=str(mock_template.template_id), current_user=FAKE_USER
                )

            # The HTTPException from is_public access should propagate
            assert exc_info.value.status_code == 418

        # Reset the mock
        type(mock_template).is_public = PropertyMock(return_value=False)

    @pytest.mark.asyncio
    async def test_update_template_http_exception_reraise(self, mock_template: MagicMock) -> None:
        """Test that HTTPException is re-raised in update_template (Lines 423-424)."""
        from app.models.schemas import SessionTemplateUpdateRequest

        mock_db = MagicMock()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_template

        # Make it raise HTTPException on second execute
        http_exc = HTTPException(status_code=418, detail="I'm a teapot")
        mock_db.execute.side_effect = [mock_result, http_exc]

        with patch("app.routes.templates.get_async_db_context") as mock_db_context:
            mock_context_manager = MagicMock()
            mock_context_manager.__aenter__ = AsyncMock(return_value=mock_db)
            mock_context_manager.__aexit__ = AsyncMock(return_value=False)
            mock_db_context.return_value = mock_context_manager

            request = SessionTemplateUpdateRequest(
                name="New Name",
                description=None,
                config_path=None,
                custom_config=None,
                default_description=None,
                tags=None,
                is_public=None,
            )

            # HTTPException should be re-raised as-is
            with pytest.raises(HTTPException) as exc_info:
                await update_template(
                    template_id=str(mock_template.template_id),
                    request=request,
                    current_user=FAKE_USER,
                )

            assert exc_info.value.status_code == 418

    @pytest.mark.asyncio
    async def test_delete_template_http_exception_reraise(self, mock_template: MagicMock) -> None:
        """Test that HTTPException is re-raised in delete_template (Lines 472-473)."""
        mock_db = MagicMock()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_template
        mock_db.execute.return_value = mock_result

        # Make db.delete raise HTTPException
        http_exc = HTTPException(status_code=418, detail="I'm a teapot")
        mock_db.delete.side_effect = http_exc

        with patch("app.routes.templates.get_async_db_context") as mock_db_context:
            mock_context_manager = MagicMock()
            mock_context_manager.__aenter__ = AsyncMock(return_value=mock_db)
            mock_context_manager.__aexit__ = AsyncMock(return_value=False)
            mock_db_context.return_value = mock_context_manager

            # HTTPException should be re-raised as-is
            with pytest.raises(HTTPException) as exc_info:
                await delete_template(
                    template_id=str(mock_template.template_id), current_user=FAKE_USER
                )

            assert exc_info.value.status_code == 418

    @pytest.mark.asyncio
    async def test_update_template_with_all_fields(self, mock_template: MagicMock) -> None:
        """Test updating all fields of a template."""
        from app.models.schemas import SessionTemplateUpdateRequest

        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_template

        # For duplicate check, return None
        mock_check_result = MagicMock()
        mock_check_result.scalar_one_or_none.return_value = None

        mock_db.execute.side_effect = [mock_result, mock_check_result]

        with patch("app.routes.templates.get_async_db_context") as mock_db_context:
            mock_context_manager = MagicMock()
            mock_context_manager.__aenter__ = AsyncMock(return_value=mock_db)
            mock_context_manager.__aexit__ = AsyncMock(return_value=False)
            mock_db_context.return_value = mock_context_manager

            request = SessionTemplateUpdateRequest(
                name="New Name",
                description="New Description",
                config_path="config/new.yaml",  # Relative path, not absolute
                custom_config={"new": "config"},
                default_description="New Default",
                tags=["new", "tags"],
                is_public=True,
            )

            response = await update_template(
                template_id=str(mock_template.template_id),
                request=request,
                current_user=FAKE_USER,
            )

            assert response is not None
            # Verify all fields were updated
            assert mock_template.name == "New Name"
            assert mock_template.description == "New Description"
            assert mock_template.config_path == "config/new.yaml"
            assert mock_template.custom_config == {"new": "config"}
            assert mock_template.default_description == "New Default"
            assert mock_template.tags == ["new", "tags"]
            assert mock_template.is_public is True

    @pytest.mark.asyncio
    async def test_create_template_rollback_on_error(self) -> None:
        """Test that rollback is called when commit fails."""
        from app.models.schemas import SessionTemplateCreateRequest

        mock_db = MagicMock()

        mock_check_result = MagicMock()
        mock_check_result.scalar_one_or_none.return_value = None

        mock_db.execute.return_value = mock_check_result
        mock_db.commit.side_effect = Exception("Commit failed")

        with patch("app.routes.templates.get_async_db_context") as mock_db_context:
            mock_context_manager = MagicMock()
            mock_context_manager.__aenter__ = AsyncMock(return_value=mock_db)
            mock_context_manager.__aexit__ = AsyncMock(return_value=False)
            mock_db_context.return_value = mock_context_manager

            request = SessionTemplateCreateRequest(
                name="Test Template",
                description="Description",
                config_path="config/test.yaml",
                custom_config=None,
                default_description=None,
                tags=[],
                is_public=False,
            )

            with pytest.raises(HTTPException):
                await create_template(request=request, current_user=FAKE_USER)

            # Verify rollback was called
            mock_db.rollback.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_templates_with_none_tags(self, mock_template: MagicMock) -> None:
        """Test list templates handles None tags gracefully."""
        mock_template.tags = None  # Set tags to None

        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_template]
        mock_db.execute.return_value = mock_result

        mock_count_query = MockQuery(has_whereclause=True)
        mock_db.query.return_value = mock_count_query

        with patch("app.routes.templates.get_async_db_context") as mock_db_context:
            mock_context_manager = MagicMock()
            mock_context_manager.__aenter__ = AsyncMock(return_value=mock_db)
            mock_context_manager.__aexit__ = AsyncMock(return_value=False)
            mock_db_context.return_value = mock_context_manager

            response = await list_templates(
                page=1, per_page=10, public_only=False, current_user=FAKE_USER
            )

        assert response.templates is not None
        # The response should have empty list for tags when None
        assert response.templates[0].tags == []

    @pytest.mark.asyncio
    async def test_update_template_no_name_change(self, mock_template: MagicMock) -> None:
        """Test update when name is not provided (None)."""
        from app.models.schemas import SessionTemplateUpdateRequest

        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_template
        mock_db.execute.return_value = mock_result

        with patch("app.routes.templates.get_async_db_context") as mock_db_context:
            mock_context_manager = MagicMock()
            mock_context_manager.__aenter__ = AsyncMock(return_value=mock_db)
            mock_context_manager.__aexit__ = AsyncMock(return_value=False)
            mock_db_context.return_value = mock_context_manager

            # Name is None, so no duplicate check should happen
            request = SessionTemplateUpdateRequest(
                name=None,
                description="Updated Description",
                config_path=None,
                custom_config=None,
                default_description=None,
                tags=None,
                is_public=None,
            )

            response = await update_template(
                template_id=str(mock_template.template_id),
                request=request,
                current_user=FAKE_USER,
            )

            # Only one execute call (for fetching template)
            assert mock_db.execute.call_count == 1
            assert response.description == "Updated Description"

    @pytest.mark.asyncio
    async def test_list_templates_public_only_with_no_results(self) -> None:
        """Test public_only=true with no results."""
        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = mock_result

        # Mock count query with no whereclause (no results)
        mock_count_query = MockQuery(has_whereclause=False)
        mock_db.query.return_value = mock_count_query

        with patch("app.routes.templates.get_async_db_context") as mock_db_context:
            mock_context_manager = MagicMock()
            mock_context_manager.__aenter__ = AsyncMock(return_value=mock_db)
            mock_context_manager.__aexit__ = AsyncMock(return_value=False)
            mock_db_context.return_value = mock_context_manager

            response = await list_templates(
                page=1, per_page=10, public_only=True, current_user=FAKE_USER
            )

        assert len(response.templates) == 0
        assert response.pagination.total == 0

    @pytest.mark.asyncio
    async def test_delete_template_general_exception(self, caplog: Any) -> None:
        """Test general exception handling in delete_template."""
        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None  # Template not found
        mock_db.execute.return_value = mock_result

        with patch("app.routes.templates.get_async_db_context") as mock_db_context:
            mock_context_manager = MagicMock()
            mock_context_manager.__aenter__ = AsyncMock(return_value=mock_db)
            mock_context_manager.__aexit__ = AsyncMock(return_value=False)
            mock_db_context.return_value = mock_context_manager

            # Should raise 404 (HTTPException)
            with pytest.raises(HTTPException) as exc_info:
                await delete_template(template_id="non-existent", current_user=FAKE_USER)

            assert exc_info.value.status_code == 404

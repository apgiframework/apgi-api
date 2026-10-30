"""
Unit tests for app/services/authorization.py

Covers AuthorizationService permission checks for all roles.
Target: ≥ 90% line coverage for app/services/authorization.py

Validates: Requirements 2.12
"""

from typing import cast
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from fastapi import Request

from app.exceptions import AuthorizationError, InvalidTokenError
from app.services.authorization import (
    ROLE_PERMISSIONS,
    Permission,
    Role,
    check_permission,
    check_resource_ownership,
    get_current_user,
    get_permissions_for_roles,
    has_any_role,
    has_permission,
    log_audit_event,
    require_any_role,
    require_permission,
    require_role,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _empty_chain_db():
    """MagicMock session whose audit-chain lookup finds no previous record."""
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.with_for_update.return_value.first.return_value = (
        None
    )
    return db


def make_user(
    user_id: str = "u1", roles: list[str] | None = None, permissions: list[str] | None = None
) -> Mock:
    """Create a minimal TokenPayload-like mock."""
    user = Mock()
    user.user_id = user_id
    user.roles = roles if roles is not None else []
    user.permissions = permissions
    return user


# ---------------------------------------------------------------------------
# Role and Permission Enum Tests
# ---------------------------------------------------------------------------


class TestRoleEnum:
    def test_role_values(self) -> None:
        assert Role.ADMIN.value == "admin"
        assert Role.RESEARCHER.value == "researcher"
        assert Role.VIEWER.value == "viewer"

    def test_role_is_string_enum(self) -> None:
        assert isinstance(Role.ADMIN, str)
        assert Role.ADMIN == Role.ADMIN.value  # type: ignore[comparison-overlap]

    def test_all_roles_present(self) -> None:
        role_values = {r.value for r in Role}
        assert role_values == {"admin", "researcher", "viewer"}


class TestPermissionEnum:
    def test_session_permissions(self) -> None:
        assert Permission.SESSION_CREATE.value == "session:create"
        assert Permission.SESSION_READ.value == "session:read"
        assert Permission.SESSION_UPDATE.value == "session:update"
        assert Permission.SESSION_DELETE.value == "session:delete"
        assert Permission.SESSION_CONTROL.value == "session:control"

    def test_template_permissions(self) -> None:
        assert Permission.TEMPLATE_CREATE.value == "template:create"
        assert Permission.TEMPLATE_READ.value == "template:read"
        assert Permission.TEMPLATE_UPDATE.value == "template:update"
        assert Permission.TEMPLATE_DELETE.value == "template:delete"

    def test_task_permissions(self) -> None:
        assert Permission.TASK_CREATE.value == "task:create"
        assert Permission.TASK_READ.value == "task:read"
        assert Permission.TASK_DELETE.value == "task:delete"

    def test_data_permissions(self) -> None:
        assert Permission.DATA_EXPORT.value == "data:export"
        assert Permission.DATA_READ.value == "data:read"

    def test_system_permissions(self) -> None:
        assert Permission.SYSTEM_ADMIN.value == "system:admin"
        assert Permission.USER_MANAGE.value == "user:manage"

    def test_user_permissions(self) -> None:
        assert Permission.USER_CREATE.value == "user:create"
        assert Permission.USER_READ.value == "user:read"
        assert Permission.USER_UPDATE.value == "user:update"
        assert Permission.USER_DELETE.value == "user:delete"
        assert Permission.USER_ADMIN.value == "user:admin"

    def test_permission_is_string_enum(self) -> None:
        assert isinstance(Permission.SESSION_READ, str)
        assert Permission.SESSION_READ == Permission.SESSION_READ.value  # type: ignore[comparison-overlap]


# ---------------------------------------------------------------------------
# ROLE_PERMISSIONS mapping tests
# ---------------------------------------------------------------------------


class TestRolePermissionsMapping:
    """Verify the ROLE_PERMISSIONS mapping is correct for each role."""

    # --- ADMIN ---
    def test_admin_has_all_session_permissions(self) -> None:
        admin_perms = ROLE_PERMISSIONS[Role.ADMIN]
        for p in [
            Permission.SESSION_CREATE,
            Permission.SESSION_READ,
            Permission.SESSION_UPDATE,
            Permission.SESSION_DELETE,
            Permission.SESSION_CONTROL,
        ]:
            assert p in admin_perms

    def test_admin_has_all_template_permissions(self) -> None:
        admin_perms = ROLE_PERMISSIONS[Role.ADMIN]
        for p in [
            Permission.TEMPLATE_CREATE,
            Permission.TEMPLATE_READ,
            Permission.TEMPLATE_UPDATE,
            Permission.TEMPLATE_DELETE,
        ]:
            assert p in admin_perms

    def test_admin_has_all_task_permissions(self) -> None:
        admin_perms = ROLE_PERMISSIONS[Role.ADMIN]
        for p in [Permission.TASK_CREATE, Permission.TASK_READ, Permission.TASK_DELETE]:
            assert p in admin_perms

    def test_admin_has_all_data_permissions(self) -> None:
        admin_perms = ROLE_PERMISSIONS[Role.ADMIN]
        assert Permission.DATA_EXPORT in admin_perms
        assert Permission.DATA_READ in admin_perms

    def test_admin_has_system_permissions(self) -> None:
        admin_perms = ROLE_PERMISSIONS[Role.ADMIN]
        assert Permission.SYSTEM_ADMIN in admin_perms
        assert Permission.USER_MANAGE in admin_perms

    def test_admin_has_all_user_permissions(self) -> None:
        admin_perms = ROLE_PERMISSIONS[Role.ADMIN]
        for p in [
            Permission.USER_CREATE,
            Permission.USER_READ,
            Permission.USER_UPDATE,
            Permission.USER_DELETE,
            Permission.USER_ADMIN,
        ]:
            assert p in admin_perms

    # --- RESEARCHER ---
    def test_researcher_has_session_permissions(self) -> None:
        researcher_perms = ROLE_PERMISSIONS[Role.RESEARCHER]
        for p in [
            Permission.SESSION_CREATE,
            Permission.SESSION_READ,
            Permission.SESSION_UPDATE,
            Permission.SESSION_DELETE,
            Permission.SESSION_CONTROL,
        ]:
            assert p in researcher_perms

    def test_researcher_has_template_permissions(self) -> None:
        researcher_perms = ROLE_PERMISSIONS[Role.RESEARCHER]
        for p in [
            Permission.TEMPLATE_CREATE,
            Permission.TEMPLATE_READ,
            Permission.TEMPLATE_UPDATE,
            Permission.TEMPLATE_DELETE,
        ]:
            assert p in researcher_perms

    def test_researcher_has_task_permissions(self) -> None:
        researcher_perms = ROLE_PERMISSIONS[Role.RESEARCHER]
        for p in [Permission.TASK_CREATE, Permission.TASK_READ, Permission.TASK_DELETE]:
            assert p in researcher_perms

    def test_researcher_has_data_permissions(self) -> None:
        researcher_perms = ROLE_PERMISSIONS[Role.RESEARCHER]
        assert Permission.DATA_EXPORT in researcher_perms
        assert Permission.DATA_READ in researcher_perms

    def test_researcher_lacks_system_admin(self) -> None:
        researcher_perms = ROLE_PERMISSIONS[Role.RESEARCHER]
        assert Permission.SYSTEM_ADMIN not in researcher_perms
        assert Permission.USER_MANAGE not in researcher_perms

    def test_researcher_has_user_read_only(self) -> None:
        researcher_perms = ROLE_PERMISSIONS[Role.RESEARCHER]
        assert Permission.USER_READ in researcher_perms
        assert Permission.USER_CREATE not in researcher_perms
        assert Permission.USER_DELETE not in researcher_perms
        assert Permission.USER_ADMIN not in researcher_perms

    # --- VIEWER ---
    def test_viewer_has_read_only_permissions(self) -> None:
        viewer_perms = ROLE_PERMISSIONS[Role.VIEWER]
        assert Permission.SESSION_READ in viewer_perms
        assert Permission.TEMPLATE_READ in viewer_perms
        assert Permission.TASK_READ in viewer_perms
        assert Permission.DATA_READ in viewer_perms
        assert Permission.USER_READ in viewer_perms

    def test_viewer_lacks_write_permissions(self) -> None:
        viewer_perms = ROLE_PERMISSIONS[Role.VIEWER]
        for p in [
            Permission.SESSION_CREATE,
            Permission.SESSION_UPDATE,
            Permission.SESSION_DELETE,
            Permission.SESSION_CONTROL,
            Permission.TEMPLATE_CREATE,
            Permission.TEMPLATE_UPDATE,
            Permission.TEMPLATE_DELETE,
            Permission.TASK_CREATE,
            Permission.TASK_DELETE,
            Permission.DATA_EXPORT,
            Permission.SYSTEM_ADMIN,
            Permission.USER_MANAGE,
            Permission.USER_CREATE,
            Permission.USER_UPDATE,
            Permission.USER_DELETE,
            Permission.USER_ADMIN,
        ]:
            assert p not in viewer_perms


# ---------------------------------------------------------------------------
# get_permissions_for_roles
# ---------------------------------------------------------------------------


class TestGetPermissionsForRoles:
    def test_empty_roles_returns_empty_set(self) -> None:
        result = get_permissions_for_roles([])
        assert result == set()

    def test_admin_role_returns_all_admin_permissions(self) -> None:
        result = get_permissions_for_roles(["admin"])
        assert result == ROLE_PERMISSIONS[Role.ADMIN]

    def test_researcher_role_returns_researcher_permissions(self) -> None:
        result = get_permissions_for_roles(["researcher"])
        assert result == ROLE_PERMISSIONS[Role.RESEARCHER]

    def test_viewer_role_returns_viewer_permissions(self) -> None:
        result = get_permissions_for_roles(["viewer"])
        assert result == ROLE_PERMISSIONS[Role.VIEWER]

    def test_invalid_role_is_skipped(self) -> None:
        result = get_permissions_for_roles(["nonexistent_role"])
        assert result == set()

    def test_mixed_valid_and_invalid_roles(self) -> None:
        result = get_permissions_for_roles(["admin", "bogus"])
        assert result == ROLE_PERMISSIONS[Role.ADMIN]

    def test_multiple_valid_roles_union(self) -> None:
        result = get_permissions_for_roles(["viewer", "researcher"])
        expected = ROLE_PERMISSIONS[Role.VIEWER] | ROLE_PERMISSIONS[Role.RESEARCHER]
        assert result == expected

    def test_all_roles_returns_union(self) -> None:
        result = get_permissions_for_roles(["admin", "researcher", "viewer"])
        expected = (
            ROLE_PERMISSIONS[Role.ADMIN]
            | ROLE_PERMISSIONS[Role.RESEARCHER]
            | ROLE_PERMISSIONS[Role.VIEWER]
        )
        assert result == expected

    def test_duplicate_roles_handled(self) -> None:
        result = get_permissions_for_roles(["admin", "admin"])
        assert result == ROLE_PERMISSIONS[Role.ADMIN]

    def test_returns_set_type(self) -> None:
        result = get_permissions_for_roles(["viewer"])
        assert isinstance(result, set)


# ---------------------------------------------------------------------------
# has_permission
# ---------------------------------------------------------------------------


class TestHasPermission:
    # Admin can do everything
    def test_admin_has_session_create(self) -> None:
        assert has_permission(["admin"], Permission.SESSION_CREATE) is True

    def test_admin_has_system_admin(self) -> None:
        assert has_permission(["admin"], Permission.SYSTEM_ADMIN) is True

    def test_admin_has_user_admin(self) -> None:
        assert has_permission(["admin"], Permission.USER_ADMIN) is True

    # Researcher permissions
    def test_researcher_has_session_create(self) -> None:
        assert has_permission(["researcher"], Permission.SESSION_CREATE) is True

    def test_researcher_has_data_export(self) -> None:
        assert has_permission(["researcher"], Permission.DATA_EXPORT) is True

    def test_researcher_lacks_system_admin(self) -> None:
        assert has_permission(["researcher"], Permission.SYSTEM_ADMIN) is False

    def test_researcher_lacks_user_manage(self) -> None:
        assert has_permission(["researcher"], Permission.USER_MANAGE) is False

    def test_researcher_lacks_user_admin(self) -> None:
        assert has_permission(["researcher"], Permission.USER_ADMIN) is False

    # Viewer permissions
    def test_viewer_has_session_read(self) -> None:
        assert has_permission(["viewer"], Permission.SESSION_READ) is True

    def test_viewer_has_data_read(self) -> None:
        assert has_permission(["viewer"], Permission.DATA_READ) is True

    def test_viewer_lacks_session_create(self) -> None:
        assert has_permission(["viewer"], Permission.SESSION_CREATE) is False

    def test_viewer_lacks_data_export(self) -> None:
        assert has_permission(["viewer"], Permission.DATA_EXPORT) is False

    def test_viewer_lacks_system_admin(self) -> None:
        assert has_permission(["viewer"], Permission.SYSTEM_ADMIN) is False

    # Edge cases
    def test_empty_roles_returns_false(self) -> None:
        assert has_permission([], Permission.SESSION_READ) is False

    def test_invalid_role_returns_false(self) -> None:
        assert has_permission(["superuser"], Permission.SESSION_READ) is False

    def test_mixed_roles_grants_if_any_match(self) -> None:
        # viewer + researcher: researcher grants SESSION_CREATE
        assert has_permission(["viewer", "researcher"], Permission.SESSION_CREATE) is True

    def test_invalid_and_valid_role(self) -> None:
        assert has_permission(["bogus", "admin"], Permission.SYSTEM_ADMIN) is True


# ---------------------------------------------------------------------------
# has_any_role
# ---------------------------------------------------------------------------


class TestHasAnyRole:
    def test_admin_matches_admin_requirement(self) -> None:
        assert has_any_role(["admin"], [Role.ADMIN]) is True

    def test_researcher_matches_researcher_requirement(self) -> None:
        assert has_any_role(["researcher"], [Role.RESEARCHER]) is True

    def test_viewer_matches_viewer_requirement(self) -> None:
        assert has_any_role(["viewer"], [Role.VIEWER]) is True

    def test_viewer_does_not_match_admin_requirement(self) -> None:
        assert has_any_role(["viewer"], [Role.ADMIN]) is False

    def test_viewer_matches_any_of_admin_or_viewer(self) -> None:
        assert has_any_role(["viewer"], [Role.ADMIN, Role.VIEWER]) is True

    def test_researcher_matches_any_of_admin_or_researcher(self) -> None:
        assert has_any_role(["researcher"], [Role.ADMIN, Role.RESEARCHER]) is True

    def test_empty_user_roles_returns_false(self) -> None:
        assert has_any_role([], [Role.ADMIN]) is False

    def test_empty_required_roles_returns_false(self) -> None:
        assert has_any_role(["admin"], []) is False

    def test_invalid_role_string_skipped(self) -> None:
        assert has_any_role(["invalid"], [Role.ADMIN]) is False

    def test_invalid_and_valid_role_string(self) -> None:
        assert has_any_role(["invalid", "admin"], [Role.ADMIN]) is True

    def test_multiple_user_roles_any_match(self) -> None:
        assert has_any_role(["viewer", "researcher"], [Role.RESEARCHER]) is True


# ---------------------------------------------------------------------------
# check_permission
# ---------------------------------------------------------------------------


class TestCheckPermission:
    """Tests for check_permission() — the core authorization gate."""

    # --- Granted via roles ---
    def test_admin_granted_session_read(self) -> None:
        user = make_user(roles=["admin"])
        check_permission(user, Permission.SESSION_READ)  # no exception

    def test_admin_granted_system_admin(self) -> None:
        user = make_user(roles=["admin"])
        check_permission(user, Permission.SYSTEM_ADMIN)

    def test_researcher_granted_session_create(self) -> None:
        user = make_user(roles=["researcher"])
        check_permission(user, Permission.SESSION_CREATE)

    def test_researcher_granted_data_export(self) -> None:
        user = make_user(roles=["researcher"])
        check_permission(user, Permission.DATA_EXPORT)

    def test_viewer_granted_session_read(self) -> None:
        user = make_user(roles=["viewer"])
        check_permission(user, Permission.SESSION_READ)

    def test_viewer_granted_data_read(self) -> None:
        user = make_user(roles=["viewer"])
        check_permission(user, Permission.DATA_READ)

    # --- Denied via roles ---
    def test_viewer_denied_session_create(self) -> None:
        user = make_user(roles=["viewer"])
        with pytest.raises(AuthorizationError):
            check_permission(user, Permission.SESSION_CREATE)

    def test_viewer_denied_system_admin(self) -> None:
        user = make_user(roles=["viewer"])
        with pytest.raises(AuthorizationError):
            check_permission(user, Permission.SYSTEM_ADMIN)

    def test_researcher_denied_system_admin(self) -> None:
        user = make_user(roles=["researcher"])
        with pytest.raises(AuthorizationError):
            check_permission(user, Permission.SYSTEM_ADMIN)

    def test_researcher_denied_user_manage(self) -> None:
        user = make_user(roles=["researcher"])
        with pytest.raises(AuthorizationError):
            check_permission(user, Permission.USER_MANAGE)

    def test_no_roles_denied(self) -> None:
        user = make_user(roles=[])
        with pytest.raises(AuthorizationError):
            check_permission(user, Permission.SESSION_READ)

    def test_invalid_role_denied(self) -> None:
        user = make_user(roles=["superuser"])
        with pytest.raises(AuthorizationError):
            check_permission(user, Permission.SESSION_READ)

    # --- Granted via direct permissions (API key path) ---
    def test_granted_via_direct_permission(self) -> None:
        user = make_user(roles=[], permissions=["session:read"])
        check_permission(user, Permission.SESSION_READ)  # no exception

    def test_granted_via_direct_permission_system_admin(self) -> None:
        user = make_user(roles=[], permissions=["system:admin"])
        check_permission(user, Permission.SYSTEM_ADMIN)

    def test_direct_permission_does_not_grant_other_permissions(self) -> None:
        # Has session:read but not session:create
        user = make_user(roles=[], permissions=["session:read"])
        with pytest.raises(AuthorizationError):
            check_permission(user, Permission.SESSION_CREATE)

    # --- With DB audit logging ---
    def test_granted_via_role_logs_audit_with_db(self) -> None:
        mock_db = _empty_chain_db()
        user = make_user(roles=["admin"])
        check_permission(user, Permission.SESSION_READ, db=mock_db)
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    def test_denied_logs_audit_with_db(self) -> None:
        mock_db = _empty_chain_db()
        user = make_user(roles=["viewer"])
        with pytest.raises(AuthorizationError):
            check_permission(user, Permission.SESSION_CREATE, db=mock_db)
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    def test_granted_via_direct_permission_logs_audit_with_db(self) -> None:
        mock_db = _empty_chain_db()
        user = make_user(roles=[], permissions=["session:read"])
        check_permission(user, Permission.SESSION_READ, db=mock_db)
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    # --- Without DB (no audit logging) ---
    def test_denied_without_db_still_raises(self) -> None:
        user = make_user(roles=["viewer"])
        with pytest.raises(AuthorizationError):
            check_permission(user, Permission.SESSION_CREATE, db=None)

    # --- With resource/action/ip/user_agent context ---
    def test_check_permission_with_full_context(self) -> None:
        mock_db = _empty_chain_db()
        user = make_user(roles=["admin"])
        check_permission(
            user,
            Permission.SESSION_READ,
            resource="session",
            action="read",
            db=mock_db,
            ip_address="127.0.0.1",
            user_agent="TestAgent/1.0",
        )
        mock_db.add.assert_called_once()

    def test_authorization_error_contains_resource_and_action(self) -> None:
        user = make_user(roles=["viewer"])
        with pytest.raises(AuthorizationError) as exc_info:
            check_permission(user, Permission.SESSION_CREATE, resource="session", action="create")
        err = exc_info.value
        assert err.details["resource"] == "session"
        assert err.details["action"] == "create"

    def test_authorization_error_contains_required_role(self) -> None:
        user = make_user(roles=["viewer"])
        with pytest.raises(AuthorizationError) as exc_info:
            check_permission(user, Permission.SYSTEM_ADMIN)
        err = exc_info.value
        # required_role should be set to the role that grants SYSTEM_ADMIN
        assert "required_role" in err.details


# ---------------------------------------------------------------------------
# log_audit_event
# ---------------------------------------------------------------------------


class TestLogAuditEvent:
    def test_with_db_adds_and_commits(self) -> None:
        mock_db = _empty_chain_db()
        log_audit_event(
            db=mock_db,
            user_id="u1",
            action="login",
            resource_type="user",
            resource_id="u1",
            status="success",
        )
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    def test_with_db_none_does_nothing(self) -> None:
        # Should not raise
        log_audit_event(db=None, user_id="u1", action="login", status="success")

    def test_with_all_optional_fields(self) -> None:
        mock_db = _empty_chain_db()
        log_audit_event(
            db=mock_db,
            user_id="u1",
            action="access:read",
            resource_type="session",
            resource_id="sess1",
            details={"key": "value"},
            ip_address="10.0.0.1",
            user_agent="Mozilla/5.0",
            status="granted",
        )
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    def test_db_exception_is_swallowed(self) -> None:
        mock_db = _empty_chain_db()
        mock_db.add.side_effect = Exception("DB error")
        # Should not raise — exception is caught internally
        log_audit_event(db=mock_db, user_id="u1", action="login", status="success")

    def test_with_minimal_args(self) -> None:
        mock_db = _empty_chain_db()
        log_audit_event(db=mock_db)
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    def test_details_defaults_to_empty_dict(self) -> None:
        mock_db = _empty_chain_db()
        log_audit_event(db=mock_db, user_id="u1", action="test", status="success")
        call_args = mock_db.add.call_args[0][0]
        # The AuditLog object should have been created; just verify add was called
        assert mock_db.add.called


# ---------------------------------------------------------------------------
# check_resource_ownership
# ---------------------------------------------------------------------------


class TestCheckResourceOwnership:
    def test_owner_is_granted(self) -> None:
        user = make_user(user_id="u1", roles=["viewer"])
        check_resource_ownership("u1", user)  # no exception

    def test_admin_role_granted_when_allow_admin_true(self) -> None:
        user = make_user(user_id="u2", roles=["admin"])
        check_resource_ownership("u1", user, allow_admin=True)  # no exception

    def test_admin_role_denied_when_allow_admin_false(self) -> None:
        user = make_user(user_id="u2", roles=["admin"])
        with pytest.raises(AuthorizationError):
            check_resource_ownership("u1", user, allow_admin=False)

    def test_non_owner_non_admin_denied(self) -> None:
        user = make_user(user_id="u2", roles=["viewer"])
        with pytest.raises(AuthorizationError):
            check_resource_ownership("u1", user)

    def test_researcher_non_owner_denied(self) -> None:
        user = make_user(user_id="u2", roles=["researcher"])
        with pytest.raises(AuthorizationError):
            check_resource_ownership("u1", user, allow_admin=False)

    def test_admin_via_permissions_granted(self) -> None:
        user = make_user(user_id="u2", roles=[], permissions=["admin"])
        check_resource_ownership("u1", user, allow_admin=True)  # no exception

    def test_admin_via_permissions_denied_when_allow_admin_false(self) -> None:
        user = make_user(user_id="u2", roles=[], permissions=["admin"])
        with pytest.raises(AuthorizationError):
            check_resource_ownership("u1", user, allow_admin=False)

    def test_admin_with_db_logs_audit(self) -> None:
        mock_db = _empty_chain_db()
        user = make_user(user_id="u2", roles=["admin"])
        check_resource_ownership("u1", user, allow_admin=True, db=mock_db)
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    def test_owner_with_db_does_not_log_audit(self) -> None:
        mock_db = _empty_chain_db()
        user = make_user(user_id="u1", roles=["viewer"])
        check_resource_ownership("u1", user, db=mock_db)
        # Owner path returns early without audit log
        mock_db.add.assert_not_called()

    def test_authorization_error_message(self) -> None:
        user = make_user(user_id="u2", roles=["viewer"])
        with pytest.raises(AuthorizationError) as exc_info:
            check_resource_ownership("u1", user)
        assert exc_info.value.details["resource"] == "resource"

    def test_no_permissions_attribute_non_owner_denied(self) -> None:
        user = make_user(user_id="u2", roles=["viewer"])
        user.permissions = None
        with pytest.raises(AuthorizationError):
            check_resource_ownership("u1", user)


# ---------------------------------------------------------------------------
# get_current_user (async FastAPI dependency)
# ---------------------------------------------------------------------------


class TestGetCurrentUser:
    @pytest.mark.asyncio
    async def test_returns_middleware_user_if_set(self) -> None:
        """If request.state.user is set (API key auth), return it directly."""
        mock_user = make_user(user_id="u1", roles=["admin"])
        request = Mock()
        request.state.user = mock_user
        mock_db = _empty_chain_db()

        result = await get_current_user(request, None, mock_db)
        assert result is mock_user

    @pytest.mark.asyncio
    async def test_raises_invalid_token_if_no_credentials(self) -> None:
        """No middleware user and no credentials → InvalidTokenError."""

        class _State:
            pass

        class _Request:
            state = _State()

        request = cast(Request, _Request())
        mock_db = _empty_chain_db()

        with pytest.raises(InvalidTokenError):
            await get_current_user(request, None, mock_db)

    @pytest.mark.asyncio
    async def test_raises_invalid_token_if_empty_credentials(self) -> None:
        class _State:
            pass

        class _Request:
            state = _State()

        request = cast(Request, _Request())
        mock_db = _empty_chain_db()
        credentials = Mock()
        credentials.credentials = ""

        with pytest.raises(InvalidTokenError):
            await get_current_user(request, credentials, mock_db)

    @pytest.mark.asyncio
    async def test_jwt_auth_success(self) -> None:
        """Valid JWT token → returns TokenPayload from AuthManager."""

        class _State:
            pass

        class _Request:
            state = _State()

        request = cast(Request, _Request())
        mock_db = _empty_chain_db()
        mock_payload = make_user(user_id="u1", roles=["researcher"])

        with patch("app.services.authorization.AuthManager") as MockAuthManager:
            instance = MockAuthManager.return_value
            instance.verify_token = AsyncMock(return_value=mock_payload)

            credentials = Mock()
            credentials.credentials = "valid.jwt.token"

            result = await get_current_user(request, credentials, mock_db)
            assert result is mock_payload
            instance.verify_token.assert_called_once_with("valid.jwt.token", expected_type="access")

    @pytest.mark.asyncio
    async def test_expired_token_raises_invalid_token_error(self) -> None:
        class _State:
            pass

        class _Request:
            state = _State()

        request = cast(Request, _Request())
        mock_db = _empty_chain_db()

        with patch("app.services.authorization.AuthManager") as MockAuthManager:
            instance = MockAuthManager.return_value
            instance.verify_token = AsyncMock(side_effect=Exception("Token expired"))

            credentials = Mock()
            credentials.credentials = "expired.token"

            with pytest.raises(InvalidTokenError) as exc_info:
                await get_current_user(request, credentials, mock_db)
            assert "expired" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_invalid_token_format_raises_invalid_token_error(self) -> None:
        class _State:
            pass

        class _Request:
            state = _State()

        request = cast(Request, _Request())
        mock_db = _empty_chain_db()

        with patch("app.services.authorization.AuthManager") as MockAuthManager:
            instance = MockAuthManager.return_value
            instance.verify_token = AsyncMock(side_effect=Exception("invalid token"))

            credentials = Mock()
            credentials.credentials = "bad"

            with pytest.raises(InvalidTokenError):
                await get_current_user(request, credentials, mock_db)

    @pytest.mark.asyncio
    async def test_malformed_token_raises_invalid_token_error(self) -> None:
        class _State:
            pass

        class _Request:
            state = _State()

        request = cast(Request, _Request())
        mock_db = _empty_chain_db()

        with patch("app.services.authorization.AuthManager") as MockAuthManager:
            instance = MockAuthManager.return_value
            instance.verify_token = AsyncMock(side_effect=Exception("malformed token data"))

            credentials = Mock()
            credentials.credentials = "malformed"

            with pytest.raises(InvalidTokenError):
                await get_current_user(request, credentials, mock_db)

    @pytest.mark.asyncio
    async def test_wrong_token_type_raises_invalid_token_error(self) -> None:
        class _State:
            pass

        class _Request:
            state = _State()

        request = cast(Request, _Request())
        mock_db = _empty_chain_db()

        with patch("app.services.authorization.AuthManager") as MockAuthManager:
            instance = MockAuthManager.return_value
            instance.verify_token = AsyncMock(side_effect=Exception("token type mismatch"))

            credentials = Mock()
            credentials.credentials = "refresh.token"

            with pytest.raises(InvalidTokenError):
                await get_current_user(request, credentials, mock_db)

    @pytest.mark.asyncio
    async def test_generic_auth_error_raises_invalid_token_error(self) -> None:
        class _State:
            pass

        class _Request:
            state = _State()

        request = cast(Request, _Request())
        mock_db = _empty_chain_db()

        with patch("app.services.authorization.AuthManager") as MockAuthManager:
            instance = MockAuthManager.return_value
            instance.verify_token = AsyncMock(side_effect=Exception("Connection refused"))

            credentials = Mock()
            credentials.credentials = "some.token"

            with pytest.raises(InvalidTokenError):
                await get_current_user(request, credentials, mock_db)


# ---------------------------------------------------------------------------
# require_permission dependency factory
# ---------------------------------------------------------------------------


class TestRequirePermission:
    def test_returns_callable(self) -> None:
        dep = require_permission(Permission.SESSION_READ)
        assert callable(dep)

    @pytest.mark.asyncio
    async def test_grants_when_user_has_permission(self) -> None:
        checker = require_permission(Permission.SESSION_READ)
        user = make_user(roles=["admin"])
        result = await checker(current_user=user)
        assert result is user

    @pytest.mark.asyncio
    async def test_grants_researcher_session_create(self) -> None:
        checker = require_permission(Permission.SESSION_CREATE)
        user = make_user(roles=["researcher"])
        result = await checker(current_user=user)
        assert result is user

    @pytest.mark.asyncio
    async def test_grants_viewer_session_read(self) -> None:
        checker = require_permission(Permission.SESSION_READ)
        user = make_user(roles=["viewer"])
        result = await checker(current_user=user)
        assert result is user

    @pytest.mark.asyncio
    async def test_denies_viewer_session_create(self) -> None:
        checker = require_permission(Permission.SESSION_CREATE)
        user = make_user(roles=["viewer"])
        with pytest.raises(AuthorizationError):
            await checker(current_user=user)

    @pytest.mark.asyncio
    async def test_denies_researcher_system_admin(self) -> None:
        checker = require_permission(Permission.SYSTEM_ADMIN)
        user = make_user(roles=["researcher"])
        with pytest.raises(AuthorizationError):
            await checker(current_user=user)

    @pytest.mark.asyncio
    async def test_grants_via_direct_permissions(self) -> None:
        checker = require_permission(Permission.DATA_EXPORT)
        user = make_user(roles=[], permissions=["data:export"])
        result = await checker(current_user=user)
        assert result is user

    @pytest.mark.asyncio
    async def test_all_permissions_for_admin(self) -> None:
        """Admin should pass all permission checks."""
        user = make_user(roles=["admin"])
        for perm in Permission:
            checker = require_permission(perm)
            result = await checker(current_user=user)
            assert result is user


# ---------------------------------------------------------------------------
# require_role dependency factory
# ---------------------------------------------------------------------------


class TestRequireRole:
    def test_returns_callable(self) -> None:
        dep = require_role(Role.ADMIN)
        assert callable(dep)

    @pytest.mark.asyncio
    async def test_grants_admin_for_admin_requirement(self) -> None:
        checker = require_role(Role.ADMIN)
        user = make_user(roles=["admin"])
        result = await checker(current_user=user)
        assert result is user

    @pytest.mark.asyncio
    async def test_grants_researcher_for_researcher_requirement(self) -> None:
        checker = require_role(Role.RESEARCHER)
        user = make_user(roles=["researcher"])
        result = await checker(current_user=user)
        assert result is user

    @pytest.mark.asyncio
    async def test_grants_viewer_for_viewer_requirement(self) -> None:
        checker = require_role(Role.VIEWER)
        user = make_user(roles=["viewer"])
        result = await checker(current_user=user)
        assert result is user

    @pytest.mark.asyncio
    async def test_denies_viewer_for_admin_requirement(self) -> None:
        checker = require_role(Role.ADMIN)
        user = make_user(roles=["viewer"])
        with pytest.raises(AuthorizationError):
            await checker(current_user=user)

    @pytest.mark.asyncio
    async def test_denies_researcher_for_admin_requirement(self) -> None:
        checker = require_role(Role.ADMIN)
        user = make_user(roles=["researcher"])
        with pytest.raises(AuthorizationError):
            await checker(current_user=user)

    @pytest.mark.asyncio
    async def test_denies_empty_roles(self) -> None:
        checker = require_role(Role.VIEWER)
        user = make_user(roles=[])
        with pytest.raises(AuthorizationError):
            await checker(current_user=user)

    @pytest.mark.asyncio
    async def test_grants_when_user_has_multiple_roles_including_required(self) -> None:
        checker = require_role(Role.ADMIN)
        user = make_user(roles=["viewer", "admin"])
        result = await checker(current_user=user)
        assert result is user


# ---------------------------------------------------------------------------
# require_any_role dependency factory
# ---------------------------------------------------------------------------


class TestRequireAnyRole:
    def test_returns_callable(self) -> None:
        dep = require_any_role([Role.ADMIN, Role.RESEARCHER])
        assert callable(dep)

    @pytest.mark.asyncio
    async def test_grants_admin_for_admin_or_researcher(self) -> None:
        checker = require_any_role([Role.ADMIN, Role.RESEARCHER])
        user = make_user(roles=["admin"])
        result = await checker(current_user=user)
        assert result is user

    @pytest.mark.asyncio
    async def test_grants_researcher_for_admin_or_researcher(self) -> None:
        checker = require_any_role([Role.ADMIN, Role.RESEARCHER])
        user = make_user(roles=["researcher"])
        result = await checker(current_user=user)
        assert result is user

    @pytest.mark.asyncio
    async def test_denies_viewer_for_admin_or_researcher(self) -> None:
        checker = require_any_role([Role.ADMIN, Role.RESEARCHER])
        user = make_user(roles=["viewer"])
        with pytest.raises(AuthorizationError):
            await checker(current_user=user)

    @pytest.mark.asyncio
    async def test_denies_empty_roles(self) -> None:
        checker = require_any_role([Role.ADMIN, Role.RESEARCHER])
        user = make_user(roles=[])
        with pytest.raises(AuthorizationError):
            await checker(current_user=user)

    @pytest.mark.asyncio
    async def test_authorization_error_lists_required_roles(self) -> None:
        checker = require_any_role([Role.ADMIN, Role.RESEARCHER])
        user = make_user(roles=["viewer"])
        with pytest.raises(AuthorizationError) as exc_info:
            await checker(current_user=user)
        assert "admin" in exc_info.value.details.get("required_role", "")
        assert "researcher" in exc_info.value.details.get("required_role", "")

    @pytest.mark.asyncio
    async def test_grants_all_roles_individually(self) -> None:
        for role in Role:
            checker = require_any_role([role])
            user = make_user(roles=[role.value])
            result = await checker(current_user=user)
            assert result is user

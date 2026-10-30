"""
Coverage tests for app/services/authorization.py
"""

from unittest.mock import MagicMock

import pytest

from app.exceptions import AuthorizationError, InvalidTokenError
from app.models.schemas import TokenPayload
from app.services.authorization import (
    Permission,
    Role,
    check_permission,
    check_resource_ownership,
    get_current_user,
    get_permissions_for_roles,
    has_any_role,
    has_permission,
    log_audit_event,
    require_permission,
    require_role,
)


def _empty_chain_db():
    """MagicMock session whose audit-chain lookup finds no previous record."""
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.with_for_update.return_value.first.return_value = (
        None
    )
    return db


@pytest.fixture
def current_user():
    return TokenPayload(
        user_id="user1",
        username="user1",
        roles=["viewer"],
        exp=None,
        token_type="access",
        jti="jti",
    )


def test_get_permissions_for_roles():
    """Test get_permissions_for_roles."""
    perms = get_permissions_for_roles(["admin", "invalid"])
    assert Permission.SESSION_CREATE in perms
    assert len(perms) > 0


def test_has_permission_invalid_role():
    """Test has_permission with invalid role."""
    assert has_permission(["invalid"], Permission.SESSION_CREATE) is False


def test_has_any_role():
    """Test has_any_role."""
    assert has_any_role(["admin"], [Role.ADMIN, Role.VIEWER]) is True
    assert has_any_role(["viewer"], [Role.ADMIN]) is False


def test_check_permission_direct_perm(current_user):
    """Test check_permission with direct permission."""
    current_user.permissions = ["session:create"]
    check_permission(current_user, Permission.SESSION_CREATE)
    # Should not raise


def test_check_permission_denied(current_user):
    """Test check_permission denied path."""
    with pytest.raises(AuthorizationError):
        check_permission(current_user, Permission.SESSION_CREATE)


def test_check_permission_audit_log(current_user):
    """Test check_permission with audit log."""
    mock_db = _empty_chain_db()
    current_user.roles = ["admin"]
    check_permission(current_user, Permission.SESSION_CREATE, db=mock_db)
    assert mock_db.add.called


@pytest.mark.asyncio
async def test_get_current_user_middleware(current_user):
    """Test get_current_user from request state."""
    request = MagicMock()
    request.state.user = current_user
    result = await get_current_user(request)
    assert result == current_user


@pytest.mark.asyncio
async def test_get_current_user_missing_token():
    """Test get_current_user missing token."""
    request = MagicMock()
    request.state.user = None
    with pytest.raises(InvalidTokenError, match="Missing authorization token"):
        await get_current_user(request, credentials=None)


@pytest.mark.asyncio
async def test_require_permission(current_user):
    """Test require_permission dependency."""
    checker = require_permission(Permission.SESSION_READ)
    # Should not raise for viewer
    await checker(current_user)


@pytest.mark.asyncio
async def test_require_role_denied(current_user):
    """Test require_role dependency denied."""
    checker = require_role(Role.ADMIN)
    with pytest.raises(AuthorizationError):
        await checker(current_user)


def test_log_audit_event_failure():
    """Test log_audit_event exception handling."""
    mock_db = _empty_chain_db()
    mock_db.add.side_effect = Exception("DB error")
    # Should not raise
    log_audit_event(mock_db, action="test")


def test_check_resource_ownership_admin(current_user):
    """Test check_resource_ownership for admin."""
    current_user.roles = ["admin"]
    check_resource_ownership("other_user", current_user)
    # Should not raise


def test_check_resource_ownership_denied(current_user):
    """Test check_resource_ownership denied."""
    current_user.user_id = "user1"
    with pytest.raises(AuthorizationError):
        check_resource_ownership("user2", current_user)

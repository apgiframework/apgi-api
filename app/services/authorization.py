"""
Authorization Service

Role-Based Access Control (RBAC) for API endpoints.
"""

import logging
import uuid
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, cast

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import settings
from app.database.connection import get_db
from app.database.models import AuditLog
from app.exceptions import AuthorizationError, InvalidTokenError
from app.models.schemas import TokenPayload as TokenPayload
from app.services.audit_signing import sign_audit_entry
from app.services.auth_manager import AuthManager

logger = logging.getLogger(__name__)

# ============================================================================
# Roles and Permissions
# ============================================================================


class Role(str, Enum):
    """User roles for RBAC."""

    ADMIN = "admin"
    RESEARCHER = "researcher"
    VIEWER = "viewer"


class Permission(str, Enum):
    """System permissions."""

    # Session permissions
    SESSION_CREATE = "session:create"
    SESSION_READ = "session:read"
    SESSION_UPDATE = "session:update"
    SESSION_DELETE = "session:delete"
    SESSION_CONTROL = "session:control"  # start, pause, stop, reset

    # Template permissions
    TEMPLATE_CREATE = "template:create"
    TEMPLATE_READ = "template:read"
    TEMPLATE_UPDATE = "template:update"
    TEMPLATE_DELETE = "template:delete"

    # Task permissions
    TASK_CREATE = "task:create"
    TASK_READ = "task:read"
    TASK_DELETE = "task:delete"

    # Data permissions
    DATA_EXPORT = "data:export"
    DATA_READ = "data:read"

    # System permissions
    SYSTEM_ADMIN = "system:admin"
    USER_MANAGE = "user:manage"

    # User management permissions (more granular)
    USER_CREATE = "user:create"
    USER_READ = "user:read"
    USER_UPDATE = "user:update"
    USER_DELETE = "user:delete"
    USER_ADMIN = "user:admin"  # Full user administration

    # Operations console scopes (S3-GOV-01). Deliberately separate from SYSTEM_ADMIN.
    OPS_READ = "ops:read"  # aggregate operational views and failure lists
    OPS_WRITE = "ops:write"  # retry jobs, acknowledge alerts, classify origin, assign partitions
    OPS_QUALITY = "ops:quality"  # scientific data-quality indicators


# Role to permissions mapping
ROLE_PERMISSIONS: Dict[Role, Set[Permission]] = {
    Role.ADMIN: {
        # Admins have all permissions
        Permission.SESSION_CREATE,
        Permission.SESSION_READ,
        Permission.SESSION_UPDATE,
        Permission.SESSION_DELETE,
        Permission.SESSION_CONTROL,
        Permission.TEMPLATE_CREATE,
        Permission.TEMPLATE_READ,
        Permission.TEMPLATE_UPDATE,
        Permission.TEMPLATE_DELETE,
        Permission.TASK_CREATE,
        Permission.TASK_READ,
        Permission.TASK_DELETE,
        Permission.DATA_EXPORT,
        Permission.DATA_READ,
        Permission.SYSTEM_ADMIN,
        Permission.USER_MANAGE,
        Permission.USER_CREATE,
        Permission.USER_READ,
        Permission.USER_UPDATE,
        Permission.USER_DELETE,
        Permission.USER_ADMIN,
        Permission.OPS_READ,
        Permission.OPS_WRITE,
        Permission.OPS_QUALITY,
    },
    Role.RESEARCHER: {
        # Researchers can create and manage their own sessions and tasks
        Permission.SESSION_CREATE,
        Permission.SESSION_READ,
        Permission.SESSION_UPDATE,
        Permission.SESSION_DELETE,
        Permission.SESSION_CONTROL,
        Permission.TEMPLATE_CREATE,
        Permission.TEMPLATE_READ,
        Permission.TEMPLATE_UPDATE,
        Permission.TEMPLATE_DELETE,
        Permission.TASK_CREATE,
        Permission.TASK_READ,
        Permission.TASK_DELETE,
        Permission.DATA_EXPORT,
        Permission.DATA_READ,
        # Limited user management (can read and update own profile)
        Permission.USER_READ,
    },
    Role.VIEWER: {
        # Viewers can only read data
        Permission.SESSION_READ,
        Permission.TEMPLATE_READ,
        Permission.TASK_READ,
        Permission.DATA_READ,
        # Can read basic user info
        Permission.USER_READ,
    },
}


# ============================================================================
# Authorization Functions
# ============================================================================


def get_permissions_for_roles(roles: List[str]) -> Set[Permission]:
    """
    Get all permissions for a list of roles.

    Args:
        roles: List of role names

    Returns:
        Set of permissions granted by these roles
    """
    permissions = set()
    for role_name in roles:
        try:
            role = Role(role_name)
            permissions.update(ROLE_PERMISSIONS.get(role, set()))
        except ValueError:
            # Unknown role, skip it
            logger.warning("Invalid role '%s' provided, skipping", role_name)
    return permissions


def has_permission(user_roles: List[str], required_permission: Permission) -> bool:
    """
    Check if any of the user's roles grants the required permission.

    Args:
        user_roles: List of role strings from the user's token
        required_permission: The permission to check for

    Returns:
        True if any role grants the permission, False otherwise
    """
    for role_str in user_roles:
        try:
            role = Role(role_str)
            if required_permission in ROLE_PERMISSIONS[role]:
                return True
        except ValueError:
            # Invalid role string, skip
            continue
    return False


def has_any_role(user_roles: List[str], required_roles: List[Role]) -> bool:
    """
    Check if user has any of the required roles.

    Args:
        user_roles: List of user's roles
        required_roles: List of required roles

    Returns:
        True if user has at least one of the required roles
    """
    for role_name in user_roles:
        try:
            role = Role(role_name)
            if role in required_roles:
                return True
        except ValueError:
            continue
    return False


def check_permission(
    current_user: TokenPayload,
    required_permission: Permission,
    resource: str = "resource",
    action: str = "access",
    db: Optional[Session] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    """
    Check if user has permission, raise exception if not.

    Args:
        current_user: Current authenticated user
        required_permission: Permission to check
        resource: Resource being accessed (for error message)
        action: Action being performed (for error message)
        db: Database session for audit logging (optional)
        ip_address: Client IP address for audit logging (optional)
        user_agent: Client user agent for audit logging (optional)

    Raises:
        AuthorizationError: If user lacks the required permission
    """
    # Check if user has permission via direct permissions (e.g., API key)
    if current_user.permissions and required_permission.value in current_user.permissions:
        # Log successful authorization
        logger.info(
            "Authorization granted via permissions",
            extra={
                "user_id": current_user.user_id,
                "permission": required_permission.value,
                "resource": resource,
                "action": action,
            },
        )

        # Log audit event if database session provided
        if db:
            log_audit_event(
                db=db,
                user_id=current_user.user_id,
                action=f"access:{action}",
                resource_type=resource,
                status="granted",
                details={
                    "permission": required_permission.value,
                    "permissions": current_user.permissions,
                },
                ip_address=ip_address,
                user_agent=user_agent,
            )
        return

    # Check via roles
    if not has_permission(current_user.roles, required_permission):
        # Log failed authorization attempt
        logger.warning(
            "Authorization denied",
            extra={
                "user_id": current_user.user_id,
                "user_roles": current_user.roles,
                "required_permission": required_permission.value,
                "resource": resource,
                "action": action,
            },
        )

        # Log audit event if database session provided
        if db:
            log_audit_event(
                db=db,
                user_id=current_user.user_id,
                action=f"access:{action}",
                resource_type=resource,
                status="denied",
                details={
                    "required_permission": required_permission.value,
                    "user_roles": current_user.roles,
                },
                ip_address=ip_address,
                user_agent=user_agent,
            )
        else:
            # Always log authorization failures, even without explicit DB session
            # This prevents silent audit log failures
            logger.error(
                "Authorization audit log failed - no database session provided",
                extra={
                    "user_id": current_user.user_id,
                    "user_roles": current_user.roles,
                    "required_permission": required_permission.value,
                    "resource": resource,
                    "action": action,
                    "ip_address": ip_address,
                    "user_agent": user_agent,
                },
            )

        # Find which role would grant this permission
        required_role = None
        for role, perms in ROLE_PERMISSIONS.items():  # pragma: no branch
            if required_permission in perms:  # pragma: no branch
                required_role = role.value
                break

        raise AuthorizationError(resource=resource, action=action, required_role=required_role)
    else:
        # Log successful authorization
        logger.info(
            "Authorization granted",
            extra={
                "user_id": current_user.user_id,
                "user_roles": current_user.roles,
                "permission": required_permission.value,
                "resource": resource,
                "action": action,
            },
        )

        # Log audit event if database session provided
        if db:
            log_audit_event(
                db=db,
                user_id=current_user.user_id,
                action=f"access:{action}",
                resource_type=resource,
                status="granted",
                details={
                    "permission": required_permission.value,
                    "user_roles": current_user.roles,
                },
                ip_address=ip_address,
                user_agent=user_agent,
            )


# ============================================================================
# FastAPI Dependencies
# ============================================================================

# HTTP Bearer token security scheme
security = HTTPBearer()


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(HTTPBearer(auto_error=False)),
    db: Session = Depends(get_db),
) -> TokenPayload:
    """
    FastAPI dependency to get current authenticated user from JWT token or API key.

    First checks if the authentication middleware has already authenticated the user
    via API key (stored in request.state.user). If not, falls back to JWT token extraction.

    Args:
        request: FastAPI request object
        credentials: HTTP authorization credentials (optional)
        db: Database session

    Returns:
        TokenPayload with user information

    Raises:
        InvalidTokenError: If token is invalid or expired
    """
    # Check if middleware already authenticated via API key
    if hasattr(request.state, "user") and request.state.user:
        return cast(TokenPayload, request.state.user)

    # Fall back to JWT token authentication
    if not credentials or not credentials.credentials:
        raise InvalidTokenError(
            "Missing authorization token. Please provide a valid JWT token in the Authorization header."
        )

    token = credentials.credentials
    auth_manager = AuthManager(db)

    try:
        payload: TokenPayload = await auth_manager.verify_token(token, expected_type="access")
        return payload
    except Exception as e:
        # Provide more helpful error messages based on the exception type
        error_msg = str(e)
        if "expired" in error_msg.lower():
            raise InvalidTokenError("Token has expired. Please log in again to get a new token.")
        elif "invalid" in error_msg.lower() or "malformed" in error_msg.lower():
            raise InvalidTokenError("Invalid token format. Please provide a valid JWT token.")
        elif "token type" in error_msg.lower():
            raise InvalidTokenError(
                "Invalid token type. Please use an access token, not a refresh token."
            )
        else:
            raise InvalidTokenError(
                "Authentication failed. Please check your credentials and try again."
            )


def require_permission(permission: Permission) -> Callable[..., Awaitable[TokenPayload]]:
    """
    Create a FastAPI dependency that requires a specific permission.

    Args:
        permission: Required permission

    Returns:
        FastAPI dependency function

    Example:
        @app.get("/sessions", dependencies=[Depends(require_permission(Permission.SESSION_READ))])
        async def list_sessions():
            ...
    """

    async def permission_checker(
        current_user: TokenPayload = Depends(get_current_user),
    ) -> TokenPayload:
        """Check if current user has required permission."""
        # Safely split permission value to handle malformed permissions
        permission_parts = permission.value.split(":", 1)  # Split only on first colon
        if len(permission_parts) != 2:
            logger.warning(
                "Invalid permission format: %s. Expected format: 'resource:action'",
                permission.value,
            )
            raise ValueError(
                f"Invalid permission format: {permission.value}. Expected format: 'resource:action'"
            )

        resource, action = permission_parts
        check_permission(
            current_user=current_user,
            required_permission=permission,
            resource=resource,
            action=action,
        )
        return current_user

    return permission_checker


def require_role(role: Role) -> Callable[..., Awaitable[TokenPayload]]:
    """
    Create a FastAPI dependency that requires a specific role.

    Args:
        role: Required role

    Returns:
        FastAPI dependency function

    Example:
        @app.delete("/users/{user_id}", dependencies=[Depends(require_role(Role.ADMIN))])
        async def delete_user(user_id: str):
            ...
    """

    async def role_checker(current_user: TokenPayload = Depends(get_current_user)) -> TokenPayload:
        """Check if current user has required role."""
        if not has_any_role(current_user.roles, [role]):
            raise AuthorizationError(resource="endpoint", action="access", required_role=role.value)
        return current_user

    return role_checker


def require_any_role(roles: List[Role]) -> Callable[..., Awaitable[TokenPayload]]:
    """
    Create a FastAPI dependency that requires any of the specified roles.

    Args:
        roles: List of acceptable roles

    Returns:
        FastAPI dependency function
    """

    async def role_checker(current_user: TokenPayload = Depends(get_current_user)) -> TokenPayload:
        """Check if current user has any of the required roles."""
        if not has_any_role(current_user.roles, roles):
            role_names = [r.value for r in roles]
            raise AuthorizationError(
                resource="endpoint",
                action="access",
                required_role=f"one of: {', '.join(role_names)}",
            )
        return current_user

    return role_checker


# ============================================================================
# Audit Logging
# ============================================================================


def log_audit_event(
    db: Optional[Session],
    user_id: Optional[str] = None,
    action: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    status: str = "success",
) -> None:
    """
    Log an audit event to the database.

    Args:
        db: Database session
        user_id: User who performed the action (optional)
        action: Action performed (e.g., 'login', 'create_session')
        resource_type: Type of resource affected (e.g., 'session', 'user')
        resource_id: ID of the affected resource (optional)
        details: Additional details about the action (optional)
        ip_address: Client IP address for audit logging (optional)
        user_agent: Client user agent for audit logging (optional)
        status: Action outcome ('success', 'failure', etc.)
    """
    if db is None:
        return

    try:
        audit_id = str(uuid.uuid4())
        signature: Optional[str] = None
        if settings.audit_signing_key:
            signature = sign_audit_entry(
                audit_id, user_id, action, resource_type, resource_id, status, details
            )
        audit_log = AuditLog(
            audit_id=audit_id,
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details or {},
            ip_address=ip_address,
            user_agent=user_agent,
            status=status,
            signature=signature,
        )
        db.add(audit_log)
        db.commit()
    except Exception as e:
        logger.error("Failed to log audit event: %s", e)
        # Don't raise exception to avoid breaking the main flow


# ============================================================================
# Resource Ownership Checks
# ============================================================================


def check_resource_ownership(
    resource_owner_id: str,
    current_user: TokenPayload,
    allow_admin: bool = True,
    db: Optional[Session] = None,
    user_id: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    """
    Check if user owns a resource or is an admin.

    Args:
        resource_owner_id: User ID of resource owner
        current_user: Current authenticated user
        allow_admin: Whether admins can access any resource
        db: Database session for audit logging (optional)
        user_id: User ID for audit logging (optional)
        ip_address: Client IP address for audit logging (optional)
        user_agent: Client user agent for audit logging (optional)

    Raises:
        AuthorizationError: If user doesn't own resource and isn't admin
    """
    # Check if user owns the resource
    if resource_owner_id == current_user.user_id:
        logger.info(
            "Resource ownership granted",
            extra={
                "user_id": current_user.user_id,
                "resource_owner_id": resource_owner_id,
                "reason": "user owns resource",
            },
        )
        return

    # Check if user is admin (if allowed)
    # For API keys, check permissions instead of roles
    is_admin = False
    if allow_admin:
        if has_any_role(current_user.roles, [Role.ADMIN]):
            is_admin = True
        elif current_user.permissions and "admin" in current_user.permissions:
            is_admin = True

    if is_admin:
        logger.info(
            "Resource ownership granted",
            extra={
                "user_id": current_user.user_id,
                "resource_owner_id": resource_owner_id,
                "reason": "user is admin",
            },
        )
        # Log audit event if database session provided
        if db:
            log_audit_event(
                db=db,
                user_id=user_id or current_user.user_id,
                action="admin_access",
                resource_type="resource",
                resource_id=resource_owner_id,
                status="granted",
                details={
                    "reason": "admin access to user-owned resource",
                    "resource_owner_id": resource_owner_id,
                },
                ip_address=ip_address,
                user_agent=user_agent,
            )
        return

    # Access denied
    logger.warning(
        "Resource ownership denied",
        extra={
            "user_id": current_user.user_id,
            "resource_owner_id": resource_owner_id,
            "user_roles": current_user.roles,
            "reason": "user does not own resource and is not admin",
        },
    )
    raise AuthorizationError(resource="resource", action="access", required_role="owner or admin")

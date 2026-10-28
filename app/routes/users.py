"""
User Management Routes

API endpoints for user management including registration,
password reset, and user administration.
"""

import logging
import secrets
from datetime import datetime, timezone
from typing import List, cast

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.database.models import User
from app.exceptions import UserNotFoundError, ValidationError
from app.models.schemas import (
    ErrorResponse,
    MFABackupCodeRegenerateResponse,
    MFABackupCodeVerifyRequest,
    MFABackupCodeVerifyResponse,
    MFADisableRequest,
    MFADisableResponse,
    MFAEnableRequest,
    MFAEnableResponse,
    MFAEnrollResponse,
    PaginationInfo,
    PasswordResetConfirmRequest,
    PasswordResetConfirmResponse,
    PasswordResetEmailRequest,
    PasswordResetEmailResponse,
    PasswordResetRequest,
    PasswordResetResponse,
    TokenPayload,
    UserCreateRequest,
    UserCreateResponse,
    UserResponse,
    UsersListResponse,
    UserStatsResponse,
    UserUpdateRequest,
)
from app.services.auth_manager import AuthManager
from app.services.authorization import (
    Permission,
    get_current_user,
    has_permission,
    require_permission,
)
from app.services.user_management import get_user_management_service

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1/users",
    tags=["User Management"],
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Forbidden"},
        404: {"model": ErrorResponse, "description": "User not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)

# Development-only; not mounted in production (see app.main).
dev_router = APIRouter(prefix="/v1/users", tags=["User Management"])


@router.post(
    "/register",
    response_model=UserCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register new user",
    description="Create a new user account with auto-generated password if not provided",
)
async def register_user(
    request: UserCreateRequest,
    db: Session = Depends(get_db),
) -> UserCreateResponse:
    """
    Register a new user.

    Args:
        request: User creation request
        db: Database session

    Returns:
        UserCreateResponse with user details and password

    Raises:
        HTTPException: If username already exists
    """
    user_service = get_user_management_service(db)

    try:
        user = user_service.create_user(
            username=request.username,
            email=request.email,
            password=request.password,
            roles=["viewer"],
        )

        return UserCreateResponse(
            user_id=str(user.user_id),
            username=str(user.username),
            email=str(user.email),
            created_at=cast(datetime, user.created_at),
            message="User created successfully. Please check your email to verify your account.",
        )

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to create user: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred",
        )


@router.get(
    "/verify-email",
    summary="Verify email address",
    description="Verify user email address using verification token",
)
async def verify_email(
    token: str,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """
    Verify user email address.

    Args:
        token: Email verification token
        db: Database session

    Returns:
        Success message

    Raises:
        HTTPException: If token is invalid or expired
    """
    user_service = get_user_management_service(db)

    # Find user with matching token
    from datetime import datetime, timezone

    user = (
        db.query(User)
        .filter(
            User.email_verification_token == token,
            User.email_verification_expires_at > datetime.now(timezone.utc),
        )
        .first()
    )

    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification token",
        )

    # Activate user and clear verification fields
    user.is_active = True  # type: ignore[assignment]
    user.email_verification_token = None  # type: ignore[assignment]
    user.email_verification_expires_at = None  # type: ignore[assignment]
    user.updated_at = datetime.now(timezone.utc)  # type: ignore[assignment]

    try:
        db.commit()
        return {"message": "Email verified successfully. You can now log in."}
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to verify email for user {user.user_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred during verification",
        )


@dev_router.post(
    "/create-default",
    response_model=UserCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create default system user",
    description="Create a default admin user for initial system setup",
    dependencies=[Depends(require_permission(Permission.USER_CREATE))],
)
async def create_default_user(
    db: Session = Depends(get_db),
) -> UserCreateResponse:
    """
    Create a default system user.

    Args:
        db: Database session
        user_service: User management service

    Returns:
        UserCreateResponse with default user details
    """
    user_service = get_user_management_service(db)

    try:
        password = secrets.token_urlsafe(32)
        user = user_service.create_default_user(
            username="admin", email="admin@example.com", password=password
        )

        return UserCreateResponse(
            user_id=user.user_id,  # type: ignore[arg-type]
            username=user.username,  # type: ignore[arg-type]
            email=user.email,  # type: ignore[arg-type]
            created_at=user.created_at,  # type: ignore[arg-type]
            message="Default user created successfully",
        )

    except Exception as e:
        logger.error(f"Failed to create default user: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred",
        )


@router.get(
    "/",
    response_model=UsersListResponse,
    dependencies=[Depends(require_permission(Permission.USER_ADMIN))],
    summary="List users",
    description="List all users with pagination",
)
async def list_users(
    page: int = 1,
    per_page: int = 10,
    active_only: bool = True,
    db: Session = Depends(get_db),
) -> UsersListResponse:
    """
    List users with pagination.

    Args:
        page: Page number (1-based)
        per_page: Items per page (max 100)
        active_only: Only return active users
        db: Database session
        user_service: User management service

    Returns:
        UsersListResponse with users and pagination info
    """
    if page < 1:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Page must be >= 1")
    if per_page < 1 or per_page > 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Per page must be between 1 and 100",
        )

    user_service = get_user_management_service(db)
    users = user_service.list_users(
        skip=(page - 1) * per_page,
        limit=per_page,
        active_only=active_only,
    )

    # Get user stats once for pagination
    stats = user_service.get_user_stats()
    total_users = stats["total_users"]

    # Calculate total count based on active_only filter
    if active_only:
        total_count = stats["active_users"]
    else:
        total_count = total_users

    return UsersListResponse(
        users=[
            UserResponse(
                user_id=user.user_id,  # type: ignore[arg-type]
                username=user.username,  # type: ignore[arg-type]
                email=user.email,  # type: ignore[arg-type]
                roles=user.roles,
                is_active=user.is_active,  # type: ignore[arg-type]
                created_at=user.created_at,  # type: ignore[arg-type]
                updated_at=user.updated_at,  # type: ignore[arg-type]
                last_login=user.last_login,  # type: ignore[arg-type]
            )
            for user in users
        ],
        pagination=PaginationInfo(page=page, per_page=per_page, total=total_count),
    )


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current user profile",
    description="Retrieve profile of currently authenticated user",
)
async def get_current_user_profile(
    current_user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserResponse:
    """
    Get current user profile.

    Args:
        current_user: Authenticated user token payload
        db: Database session
        user_service: User management service

    Returns:
        UserResponse object
    """
    user_service = get_user_management_service(db)
    user = user_service.get_user(current_user.user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    return UserResponse(
        user_id=user.user_id,  # type: ignore[arg-type]
        username=user.username,  # type: ignore[arg-type]
        email=user.email,  # type: ignore[arg-type]
        roles=user.roles,
        is_active=user.is_active,  # type: ignore[arg-type]
        created_at=user.created_at,  # type: ignore[arg-type]
        updated_at=user.updated_at,  # type: ignore[arg-type]
        last_login=user.last_login,  # type: ignore[arg-type]
    )


@router.get(
    "/stats",
    response_model=UserStatsResponse,
    summary="Get user statistics",
    description="Retrieve user statistics (requires admin privileges)",
    dependencies=[Depends(require_permission(Permission.USER_ADMIN))],
)
async def get_user_stats(
    db: Session = Depends(get_db),
) -> UserStatsResponse:
    """
    Get user statistics.

    Args:
        db: Database session
        user_service: User management service

    Returns:
        UserStatsResponse object
    """
    user_service = get_user_management_service(db)
    stats = user_service.get_user_stats()

    return UserStatsResponse(
        total_users=stats["total_users"],
        active_users=stats["active_users"],
        inactive_users=stats["inactive_users"],
        role_counts=stats["role_counts"],
        total_sessions=stats["total_sessions"],
        active_sessions=stats["active_sessions"],
    )


@router.get(
    "/{user_id}",
    response_model=UserResponse,
    summary="Get user by ID",
    description="Retrieve specific user information (requires admin privileges)",
    dependencies=[Depends(require_permission(Permission.USER_READ))],
)
async def get_user(
    user_id: str,
    db: Session = Depends(get_db),
) -> UserResponse:
    """
    Get user by ID.

    Args:
        user_id: User identifier
        db: Database session
        user_service: User management service

    Returns:
        UserResponse object

    Raises:
        HTTPException: If user not found
    """
    user_service = get_user_management_service(db)
    user = user_service.get_user(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    return UserResponse(
        user_id=user.user_id,  # type: ignore[arg-type]
        username=user.username,  # type: ignore[arg-type]
        email=user.email,  # type: ignore[arg-type]
        roles=user.roles,
        is_active=user.is_active,  # type: ignore[arg-type]
        created_at=user.created_at,  # type: ignore[arg-type]
        updated_at=user.updated_at,  # type: ignore[arg-type]
        last_login=user.last_login,  # type: ignore[arg-type]
    )


@router.patch(
    "/{user_id}",
    response_model=UserResponse,
    summary="Partially update user",
    description="Partially update user information (requires admin privileges or own user)",
)
async def partial_update_user(
    user_id: str,
    request: UserUpdateRequest,
    db: Session = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> UserResponse:
    """
    Partially update user information.

    Args:
        user_id: User identifier
        request: User update request
        db: Database session
        user_service: User management service
        current_user: Authenticated user

    Returns:
        Updated UserResponse object

    Raises:
        HTTPException: If user not found or unauthorized
    """
    user_service = get_user_management_service(db)
    # Check permissions (admin can update any user, users can only update themselves)
    is_admin = has_permission(current_user.roles, Permission.USER_ADMIN)
    is_own_user = current_user.user_id == user_id

    if not (is_admin or is_own_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to update this user",
        )

    try:
        user = user_service.update_user(
            user_id=user_id,
            email=request.email,
            roles=request.roles if is_admin else None,  # Only admins can change roles
        )
        return UserResponse(
            user_id=user.user_id,  # type: ignore[arg-type]
            username=user.username,  # type: ignore[arg-type]
            email=user.email,  # type: ignore[arg-type]
            roles=user.roles,
            is_active=user.is_active,  # type: ignore[arg-type]
            created_at=user.created_at,  # type: ignore[arg-type]
            updated_at=user.updated_at,  # type: ignore[arg-type]
            last_login=user.last_login,  # type: ignore[arg-type]
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"User not found: {e}")


@router.put(
    "/{user_id}",
    response_model=UserResponse,
    summary="Update user",
    description="Update user information (requires admin privileges or own user)",
)
async def update_user(
    user_id: str,
    request: UserUpdateRequest,
    db: Session = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> UserResponse:
    """
    Update user information.

    Args:
        user_id: User identifier
        request: User update request
        db: Database session
        user_service: User management service
        current_user: Authenticated user

    Returns:
        Updated UserResponse object

    Raises:
        HTTPException: If user not found or unauthorized
    """
    user_service = get_user_management_service(db)
    # Check permissions (admin can update any user, users can only update themselves)
    is_admin = has_permission(current_user.roles, Permission.USER_ADMIN)
    is_own_user = current_user.user_id == user_id

    if not (is_admin or is_own_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to update this user",
        )

    try:
        user = user_service.update_user(
            user_id=user_id,
            email=request.email,
            roles=request.roles if is_admin else None,  # Only admins can change roles
        )

        # Audit log role changes
        if request.roles is not None and is_admin:
            logger.info(
                f"User {user_id} roles updated to {request.roles} by admin {current_user.user_id}"
            )

        return UserResponse(
            user_id=user.user_id,  # type: ignore[arg-type]
            username=user.username,  # type: ignore[arg-type]
            email=user.email,  # type: ignore[arg-type]
            roles=user.roles,
            is_active=user.is_active,  # type: ignore[arg-type]
            created_at=user.created_at,  # type: ignore[arg-type]
            updated_at=user.updated_at,  # type: ignore[arg-type]
            last_login=user.last_login,  # type: ignore[arg-type]
        )

    except UserNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    except Exception as e:
        logger.error(f"Failed to update user: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred",
        )


@router.post(
    "/reset-password",
    response_model=PasswordResetEmailResponse,
    summary="Request password reset",
    description="Request a password reset token to be sent to the user's email",
)
async def request_password_reset(
    request: PasswordResetEmailRequest,
    db: Session = Depends(get_db),
) -> PasswordResetEmailResponse:
    """
    Request password reset by email.

    Args:
        request: Password reset email request
        db: Database session

    Returns:
        PasswordResetEmailResponse with confirmation message

    Raises:
        HTTPException: If user not found or email sending fails
    """
    user_service = get_user_management_service(db)

    try:
        user_service.request_password_reset(request.email)

        return PasswordResetEmailResponse(
            message="If an account with that email exists, a password reset link has been sent."
        )

    except UserNotFoundError:
        # Return success message to avoid email enumeration
        return PasswordResetEmailResponse(
            message="If an account with that email exists, a password reset link has been sent."
        )
    except Exception as e:
        logger.error(f"Failed to request password reset: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred",
        )


@router.post(
    "/reset-password/confirm",
    response_model=PasswordResetConfirmResponse,
    summary="Confirm password reset",
    description="Confirm password reset using the token and set new password",
)
async def confirm_password_reset(
    request: PasswordResetConfirmRequest,
    db: Session = Depends(get_db),
) -> PasswordResetConfirmResponse:
    """
    Confirm password reset with token.

    Args:
        request: Password reset confirmation request with token and new password
        db: Database session

    Returns:
        PasswordResetConfirmResponse with confirmation message

    Raises:
        HTTPException: If token is invalid or expired
    """
    user_service = get_user_management_service(db)

    try:
        user_service.confirm_password_reset(request.token, request.new_password)

        return PasswordResetConfirmResponse(
            message="Password has been reset successfully. You can now log in with your new password."
        )

    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"Failed to confirm password reset: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred",
        )


@router.post(
    "/{user_id}/reset-password",
    response_model=PasswordResetResponse,
    summary="Reset user password",
    description="Reset user password (requires admin privileges or own user)",
)
async def reset_user_password(
    user_id: str,
    request: PasswordResetRequest,
    db: Session = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> PasswordResetResponse:
    """
    Reset user password.

    Args:
        user_id: User identifier
        request: Password reset request
        db: Database session
        user_service: User management service
        current_user: Authenticated user

    Returns:
        PasswordResetResponse with new password

    Raises:
        HTTPException: If user not found or unauthorized
    """
    user_service = get_user_management_service(db)
    # Check permissions
    is_admin = has_permission(current_user.roles, Permission.USER_ADMIN)
    is_own_user = current_user.user_id == user_id

    if not (is_admin or is_own_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to reset this user's password",
        )

    try:
        new_password = user_service.reset_password(
            user_id=user_id, new_password=request.new_password
        )

        return PasswordResetResponse(user_id=user_id, message="Password reset successfully")

    except UserNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    except Exception as e:
        logger.error(f"Failed to reset password: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred",
        )


@router.post(
    "/mfa/enroll",
    response_model=MFAEnrollResponse,
    summary="Enroll MFA",
    description="Generate MFA secret and QR code for the authenticated user",
)
async def enroll_mfa(
    db: Session = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> MFAEnrollResponse:
    """
    Enroll MFA for the current user.

    Returns MFA secret and QR code URL for setting up TOTP authentication.

    Args:
        db: Database session
        current_user: Authenticated user

    Returns:
        MFAEnrollResponse with secret and QR code
    """
    user_service = get_user_management_service(db)

    try:
        # Generate MFA secret and QR code
        auth_manager = AuthManager(db)
        secret = auth_manager.generate_mfa_secret()
        qr_url = auth_manager.get_mfa_qr_url(current_user.username, secret)

        # Generate backup codes (10 codes, 32 hex characters each = 128-bit entropy)
        import hashlib
        import secrets

        backup_codes = [secrets.token_hex(16).upper() for _ in range(10)]
        # Hash backup codes before storing (never store plaintext)
        hashed_backup_codes = [hashlib.sha256(code.encode()).hexdigest() for code in backup_codes]

        # Save MFA secret and hashed backup codes to user (but don't enable yet - user needs to verify first)
        user = user_service.get_user(current_user.user_id)
        user.mfa_secret = secret  # type: ignore[assignment]
        user.mfa_backup_codes = hashed_backup_codes  # type: ignore[assignment]
        user.mfa_enabled = False  # type: ignore[assignment]
        user.updated_at = datetime.now(timezone.utc)  # type: ignore[assignment]

        db.commit()

        return MFAEnrollResponse(
            secret=secret,
            qr_code_url=qr_url,
            message="Scan the QR code with your authenticator app, then use the generated code to complete setup. Save these backup codes in a secure place - you can use them to recover access if you lose your device.",
            backup_codes=backup_codes,
        )

    except Exception as e:
        db.rollback()
        logger.error(f"Failed to enroll MFA for user {current_user.user_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred during MFA enrollment",
        )


@router.post(
    "/mfa/enable",
    response_model=MFAEnableResponse,
    summary="Enable MFA",
    description="Enable MFA after verifying the provided TOTP code",
)
async def enable_mfa(
    request: MFAEnableRequest,
    db: Session = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> MFAEnableResponse:
    """
    Enable MFA for the current user after verifying the code.

    Args:
        request: MFA enable request with TOTP code
        db: Database session
        current_user: Authenticated user

    Returns:
        MFAEnableResponse with success message
    """
    user_service = get_user_management_service(db)

    try:
        user = user_service.get_user(current_user.user_id)

        if not user.mfa_secret:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MFA not enrolled. Please enroll first.",
            )

        # Verify the code
        auth_manager = AuthManager(db)
        if not auth_manager.verify_mfa_code(user.mfa_secret, request.code):  # type: ignore[arg-type]
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid MFA code",
            )

        # Enable MFA
        user.mfa_enabled = True  # type: ignore[assignment]
        user.updated_at = datetime.now(timezone.utc)  # type: ignore[assignment]

        db.commit()

        return MFAEnableResponse(message="MFA enabled successfully")

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to enable MFA for user {current_user.user_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred during MFA enable",
        )


@router.post(
    "/mfa/disable",
    response_model=MFADisableResponse,
    summary="Disable MFA",
    description="Disable MFA for the authenticated user",
)
async def disable_mfa(
    request: MFADisableRequest,
    db: Session = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> MFADisableResponse:
    """
    Disable MFA for the current user.

    Args:
        request: MFA disable request with password verification
        db: Database session
        current_user: Authenticated user

    Returns:
        MFADisableResponse with confirmation
    """
    user_service = get_user_management_service(db)

    try:
        user = user_service.get_user(current_user.user_id)

        if not user.mfa_enabled:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MFA is not enabled",
            )

        # Verify password
        auth_manager = AuthManager(db)
        if not auth_manager.verify_password(request.password, user.password_hash):  # type: ignore[arg-type]
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid password",
            )

        # Disable MFA
        user.mfa_enabled = False  # type: ignore[assignment]
        user.mfa_secret = None  # type: ignore[assignment]
        user.mfa_backup_codes = None  # type: ignore[assignment]
        user.updated_at = datetime.now(timezone.utc)  # type: ignore[assignment]

        db.commit()

        return MFADisableResponse(message="MFA disabled successfully")

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to disable MFA for user {current_user.user_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred during MFA disable",
        )


@router.post(
    "/mfa/backup-code/verify",
    response_model=MFABackupCodeVerifyResponse,
    summary="Verify MFA backup code",
    description="Verify a backup code for MFA recovery when TOTP is unavailable",
)
async def verify_mfa_backup_code(
    request: MFABackupCodeVerifyRequest,
    db: Session = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> MFABackupCodeVerifyResponse:
    """
    Verify MFA backup code for recovery.

    Args:
        request: MFA backup code verification request
        db: Database session
        current_user: Authenticated user

    Returns:
        MFABackupCodeVerifyResponse with verification result

    Raises:
        HTTPException: If backup code is invalid or MFA not enabled
    """
    user_service = get_user_management_service(db)

    try:
        user = user_service.get_user(current_user.user_id)

        if not user.mfa_enabled:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MFA is not enabled for this account",
            )

        if not user.mfa_backup_codes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No backup codes available for this account",
            )

        # Hash the submitted code and compare with stored hashes
        import hashlib
        import hmac

        hashed_code = hashlib.sha256(request.code.encode()).hexdigest()

        # Check if the backup code exists using constant-time comparison
        code_found = False
        backup_codes = list(cast(List[str], user.mfa_backup_codes or []))
        for stored_code in backup_codes:
            if hmac.compare_digest(hashed_code, str(stored_code)):
                code_found = True
                user.mfa_backup_codes.remove(stored_code)
                user.updated_at = datetime.now(timezone.utc)  # type: ignore[assignment]
                db.commit()
                break

        if code_found:
            return MFABackupCodeVerifyResponse(
                message="Backup code verified successfully. You can now log in."
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid backup code",
            )

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to verify MFA backup code for user {current_user.user_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred during backup code verification",
        )


@router.post(
    "/mfa/backup-code/regenerate",
    response_model=MFABackupCodeRegenerateResponse,
    summary="Regenerate MFA backup codes",
    description="Generate new backup codes for MFA recovery",
)
async def regenerate_mfa_backup_codes(
    db: Session = Depends(get_db),
    current_user: TokenPayload = Depends(get_current_user),
) -> MFABackupCodeRegenerateResponse:
    """
    Regenerate MFA backup codes.

    Args:
        db: Database session
        current_user: Authenticated user

    Returns:
        MFABackupCodeRegenerateResponse with new backup codes

    Raises:
        HTTPException: If MFA not enabled
    """
    user_service = get_user_management_service(db)

    try:
        user = user_service.get_user(current_user.user_id)

        if not user.mfa_enabled:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="MFA must be enabled to regenerate backup codes",
            )

        # Generate new backup codes (10 codes, 32 hex characters each = 128-bit entropy)
        import hashlib
        import secrets

        new_backup_codes = [secrets.token_hex(16).upper() for _ in range(10)]
        # Hash backup codes before storing (never store plaintext)
        hashed_backup_codes = [
            hashlib.sha256(code.encode()).hexdigest() for code in new_backup_codes
        ]

        # Update user with new hashed backup codes
        user.mfa_backup_codes = hashed_backup_codes  # type: ignore[assignment]
        user.updated_at = datetime.now(timezone.utc)  # type: ignore[assignment]

        db.commit()

        return MFABackupCodeRegenerateResponse(
            backup_codes=new_backup_codes,
            message="Backup codes regenerated successfully. Save these codes in a secure place - you can use them to recover access if you lose your device.",
        )

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(
            f"Failed to regenerate MFA backup codes for user {current_user.user_id}: {str(e)}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred during backup code regeneration",
        )


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete user",
    description="Delete user account (requires admin privileges)",
    dependencies=[Depends(require_permission(Permission.USER_DELETE))],
)
async def delete_user(
    user_id: str,
    db: Session = Depends(get_db),
) -> None:
    """
    Delete user.

    Args:
        user_id: User identifier
        db: Database session
        user_service: User management service

    Raises:
        HTTPException: If user not found
    """
    user_service = get_user_management_service(db)
    try:
        deleted = user_service.delete_user(user_id)
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    except Exception as e:
        logger.error(f"Failed to delete user: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred",
        )

"""
Authentication Middleware

Middleware to extract and verify JWT tokens from Authorization headers.
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from app.database.connection import SessionLocal
from app.database.models import APIKey
from app.exceptions import ExpiredTokenError, InvalidTokenError
from app.models.schemas import TokenPayload
from app.services.auth_manager import AuthManager

logger = logging.getLogger(__name__)


@dataclass
class APIKeyInfo:
    """Information about an authenticated API key."""

    key_id: str
    user_id: str
    name: str
    permissions: list[str]
    created_at: datetime
    expires_at: Optional[datetime] = None


class AuthenticationMiddleware(BaseHTTPMiddleware):
    """
    Middleware to extract and verify JWT tokens and API keys from request headers.

    This middleware supports two authentication methods:
    1. JWT Bearer tokens (Authorization: Bearer <token>)
    2. API keys (X-API-Key: <key>)

    The middleware:
    1. Extracts credentials from request headers
    2. Verifies the credentials (JWT or API key)
    3. Attaches user identity to request state
    4. Handles token/key expiration and invalid credentials

    Public endpoints (those not requiring authentication) are excluded from verification.
    """

    # Endpoints that don't require authentication
    PUBLIC_PATHS = {
        "/",
        "/health",
        "/health/ready",
        "/health/live",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/metrics",
        "/version",
        "/v1/auth/login",
        "/v1/auth/refresh",
        "/v1/users/register",
        "/v1/users/verify-email",
        "/v1/users/reset-password",
        "/v1/users/reset-password/confirm",
        "/v1/payments/webhook",
        "/v1/meta",
        "/updates.json",
        "/console",
        "/console/",
        "/console/console.js",
        "/console/console.css",
        "/v1/dataset-card",
        "/v1/explorer/config",
        "/v1/consent-texts",
        "/v1/instruments/apgi-explorer-35",
        "/v1/instruments/apgi-explorer-35/score-allowlist",
        "/v1/instruments/apgi-explorer-35/versions/1.0.0",
    }

    # Versioned reference-dataset cards are public, like /v1/dataset-card.
    _PUBLIC_CARD_PATH = re.compile(r"/v1/reference-datasets/[A-Za-z0-9._-]+/card")

    _redis_client = None

    def __init__(self, app: ASGIApp) -> None:
        """
        Initialize authentication middleware.

        Args:
            app: FastAPI application
        """
        super().__init__(app)

    @classmethod
    def set_redis_client(cls, redis_client: Any) -> None:
        """
        Set Redis client for token revocation checking.

        Args:
            redis_client: Redis client instance
        """
        cls._redis_client = redis_client

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """
        Process request and verify authentication.

        Args:
            request: Incoming HTTP request
            call_next: Next middleware/handler in chain

        Returns:
            Response from next handler or error response
        """
        # Skip authentication for public paths
        if self._is_public_path(request.url.path):
            return await call_next(request)

        # Try to extract and verify credentials (JWT or API key)
        auth_result = await self._extract_and_verify_credentials(request)

        if auth_result is None:
            # Check if any authentication headers were provided
            has_auth_attempt = "Authorization" in request.headers or "X-API-Key" in request.headers
            if has_auth_attempt:
                # Invalid credentials provided - return 401
                return self._create_error_response(
                    401, "invalid_credentials", "Invalid authentication credentials"
                )
            else:
                # BUG-005 fix: deny-by-default posture.
                # No credentials supplied for a non-public path → reject immediately.
                # This adds a defence-in-depth layer: even if an endpoint accidentally
                # omits Depends(get_current_user), the middleware still blocks anonymous
                # access.  Add to PUBLIC_PATHS any intentionally-public endpoint that
                # requires no auth header at all.
                return self._create_error_response(
                    401, "authentication_required", "Authentication required"
                )

        # Attach authentication information to request state
        auth_type, user_payload = auth_result
        request.state.user = user_payload
        request.state.authenticated = True
        request.state.auth_type = auth_type

        # Continue to next handler
        response = await call_next(request)
        return response

    def _is_public_path(self, path: str) -> bool:
        """
        Check if path is public (doesn't require authentication).

        Args:
            path: Request path

        Returns:
            True if path is public, False otherwise
        """
        # Check exact matches only to prevent authentication bypass via path prefixes
        if path in self.PUBLIC_PATHS:
            return True
        if self._PUBLIC_CARD_PATH.fullmatch(path):
            return True

        return False

    async def _extract_and_verify_credentials(
        self, request: Request
    ) -> Optional[tuple[str, TokenPayload]]:
        """
        Extract and verify authentication credentials from request.

        Tries JWT Bearer token first, then API key.

        Args:
            request: HTTP request

        Returns:
            Tuple of (auth_type, user_payload) if valid credentials found, None otherwise
        """
        # Try JWT Bearer token first
        token = self._extract_token(request)
        if token:
            try:
                user_payload = await self._verify_token(token)
                return ("jwt", user_payload)
            except (ExpiredTokenError, InvalidTokenError):
                # Token invalid, continue to check API key
                pass

        # Try API key
        api_key = request.headers.get("X-API-Key")
        if api_key:
            try:
                api_key_info = await self._verify_api_key(api_key)
                # Convert API key info to TokenPayload format for compatibility
                user_payload = TokenPayload(
                    user_id=api_key_info.user_id,
                    username="",  # API keys don't have usernames
                    roles=[],  # API keys have no roles, permissions are checked separately
                    exp=datetime.now(timezone.utc)
                    + timedelta(hours=1),  # API keys are session-based
                    token_type="api_key",  # nosec: B106 - token type, not password
                    permissions=api_key_info.permissions,
                )
                return ("api_key", user_payload)
            except ValueError:
                # API key invalid, authentication failed
                pass

        return None

    def _extract_token(self, request: Request) -> Optional[str]:
        """
        Extract JWT token from Authorization header.

        Args:
            request: HTTP request

        Returns:
            Token string if present, None otherwise
        """
        auth_header = request.headers.get("Authorization")

        if not auth_header:
            return None

        # Expected format: "Bearer <token>"
        parts = auth_header.split()

        if len(parts) != 2 or parts[0].lower() != "bearer":
            return None

        return parts[1]

    async def _verify_token(self, token: str) -> TokenPayload:
        """
        Verify JWT token and extract payload.

        Args:
            token: JWT token string

        Returns:
            TokenPayload with user information

        Raises:
            InvalidTokenError: If token is invalid
            ExpiredTokenError: If token has expired
        """
        # First do blocking JWT decode
        loop = asyncio.get_running_loop()
        payload = await loop.run_in_executor(None, self._decode_and_validate_token, token)

        # Then check Redis for revocation asynchronously
        if payload.jti and self._redis_client:
            revoked = await self._redis_client.exists(f"revoked_access_tokens:{payload.jti}")
            if revoked:
                raise InvalidTokenError("Token has been revoked")

        return payload

    def _decode_and_validate_token(self, token: str) -> TokenPayload:
        """
        Blocking JWT decode and basic validation (without revocation check).
        """
        from datetime import datetime, timezone

        import jwt

        from app.config import settings as _settings
        from app.services.auth_manager import TokenPayload

        try:
            secret_key = _settings.jwt_secret_key
            if not secret_key:
                raise InvalidTokenError("JWT secret key not configured")

            # Decode token synchronously
            payload_dict = jwt.decode(
                token,
                secret_key,
                algorithms=["HS256"],
            )

            # Parse payload
            payload = TokenPayload.from_dict(payload_dict)

            # Verify token type
            if payload.token_type != "access":  # nosec: B105 - token type, not password
                raise InvalidTokenError(
                    f"Invalid token type: expected access, got {payload.token_type}"
                )

            # Check expiration
            if datetime.now(timezone.utc) > payload.exp:
                raise ExpiredTokenError("Token has expired")

            return payload

        except jwt.ExpiredSignatureError:
            raise ExpiredTokenError("Token has expired")
        except jwt.InvalidTokenError as e:
            raise InvalidTokenError(f"Invalid token: {str(e)}")
        except (ExpiredTokenError, InvalidTokenError):
            raise
        except Exception as e:
            raise InvalidTokenError(f"Token verification failed: {str(e)}")

    async def _verify_api_key(self, api_key: str) -> APIKeyInfo:
        """
        Verify API key and return key information.

        Args:
            api_key: API key string

        Returns:
            APIKeyInfo with key details

        Raises:
            ValueError: If API key is invalid or expired
        """
        loop = asyncio.get_running_loop()
        api_key_info = await loop.run_in_executor(None, self._blocking_verify_api_key, api_key)
        return api_key_info

    def _blocking_verify_api_key(self, api_key: str) -> APIKeyInfo:
        """
        Blocking version of API key verification for use in executor.
        """
        # Create database session for API key verification
        db = SessionLocal()
        try:
            auth_manager = AuthManager(db)

            # Compute HMAC prefix for fast lookup
            import hashlib
            import hmac

            if auth_manager.secret_key is None:
                raise ValueError("Secret key not configured")
            prefix = hmac.new(
                auth_manager.secret_key.encode(), api_key.encode(), hashlib.sha256
            ).hexdigest()[:16]

            # Query API keys by prefix (should significantly reduce the number of candidates)
            from datetime import datetime, timezone

            from sqlalchemy import or_

            now = datetime.now(timezone.utc)
            active_keys = (
                db.query(APIKey)
                .filter(
                    APIKey.is_active.is_(True),
                    APIKey.key_prefix == prefix,
                    or_(APIKey.expires_at.is_(None), APIKey.expires_at > now),
                )
                .all()
            )

            # If no keys, the key is invalid
            if not active_keys:
                raise ValueError("Invalid API key")

            # Find the specific key by prefix and bcrypt check
            db_api_key = None
            for candidate_key in active_keys:
                if hmac.compare_digest(
                    str(candidate_key.key_prefix), prefix
                ) and auth_manager.verify_password(
                    api_key, candidate_key.key_hash  # type: ignore[arg-type]
                ):
                    db_api_key = candidate_key
                    break

            if not db_api_key:
                raise ValueError("Invalid API key")

            # Check expiration
            if db_api_key.expires_at and db_api_key.expires_at < datetime.now(timezone.utc):
                raise ValueError("API key has expired")

            # Update last used timestamp
            try:
                db_api_key.last_used_at = datetime.now(timezone.utc)  # type: ignore[assignment]
                db.commit()
            except Exception as e:
                # Log but don't fail authentication for this
                logger.warning(
                    f"Failed to update last_used_at for API key {db_api_key.key_id}: {e}"
                )
                db.rollback()

            return APIKeyInfo(
                key_id=db_api_key.key_id,  # type: ignore[arg-type]
                user_id=db_api_key.user_id,  # type: ignore[arg-type]
                name=db_api_key.name,  # type: ignore[arg-type]
                permissions=db_api_key.permissions,  # type: ignore[arg-type]
                created_at=db_api_key.created_at,  # type: ignore[arg-type]
                expires_at=db_api_key.expires_at,  # type: ignore[arg-type]
            )

        finally:
            db.close()

    def _create_error_response(self, status_code: int, code: str, message: str) -> JSONResponse:
        """
        Create standardized error response.

        Args:
            status_code: HTTP status code
            code: Error code
            message: Error message

        Returns:
            JSONResponse with error details
        """
        return JSONResponse(
            status_code=status_code,
            content={
                "error": {
                    "code": code,
                    "message": message,
                    "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                }
            },
            headers={"WWW-Authenticate": "Bearer"} if status_code == 401 else {},
        )


def get_current_user_from_request(request: Request) -> Optional[TokenPayload]:
    """
    Get current authenticated user from request state.

    This is a helper function to access the user attached by the middleware.

    Args:
        request: HTTP request

    Returns:
        TokenPayload if user is authenticated, None otherwise
    """
    return getattr(request.state, "user", None)


def is_authenticated(request: Request) -> bool:
    """
    Check if request is authenticated.

    Args:
        request: HTTP request

    Returns:
        True if authenticated, False otherwise
    """
    return getattr(request.state, "authenticated", False)

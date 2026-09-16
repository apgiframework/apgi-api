"""
Authentication Manager Service

Handles JWT token creation/verification and password hashing for user authentication.
"""

import hmac
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, cast

import bcrypt
import jwt
import pyotp
from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.config import settings
from app.database.models import RefreshToken, User
from app.exceptions import AuthenticationError, ExpiredTokenError, InvalidTokenError
from app.models.schemas import TokenPayload

__all__ = ["AuthManager", "TokenPayload"]

logger = logging.getLogger(__name__)


class AuthManager:
    """
    Manages authentication and authorization.

    Responsibilities:
    - JWT token creation and verification
    - Password hashing and verification
    - User authentication
    - Token refresh
    """

    def __init__(self, db: Session, redis_client: Optional[Any] = None):
        """
        Initialize AuthManager.

        Args:
            db: Database session for user lookups
            redis_client: Redis client for token blocklist (optional)
        """
        self.db = db
        self.redis = redis_client
        self.secret_key = settings.jwt_secret_key
        self.algorithm = settings.jwt_algorithm
        self.access_token_expire_minutes = settings.jwt_access_token_expire_minutes
        self.refresh_token_expire_days = settings.jwt_refresh_token_expire_days

    # ========================================================================
    # Password Hashing
    # ========================================================================

    @staticmethod
    def hash_password(password: str) -> str:
        """
        Hash a password using bcrypt with SHA-256 pre-hashing to preserve full entropy.

        Args:
            password: Plain text password to hash

        Returns:
            Hashed password string

        Raises:
            ValueError: If password is empty
        """
        if not password:
            raise ValueError("Password cannot be empty")

        # Pre-hash password with SHA-256 to preserve full entropy for passwords > 72 bytes
        import hashlib

        password_hash = hashlib.sha256(password.encode("utf-8")).digest()

        salt = bcrypt.gensalt(rounds=12)
        hashed = bcrypt.hashpw(password_hash, salt)
        return hashed.decode("utf-8")

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """
        Verify a password against its hash using SHA-256 pre-hashing.

        Args:
            plain_password: Plain text password to verify
            hashed_password: Hashed password to compare against

        Returns:
            True if password matches, False otherwise
        """
        # Pre-hash password with SHA-256 to preserve full entropy for passwords > 72 bytes
        import hashlib

        plain_password_hash = hashlib.sha256(plain_password.encode("utf-8")).digest()

        hashed_password_bytes = hashed_password.encode("utf-8")
        return bcrypt.checkpw(plain_password_hash, hashed_password_bytes)

    # ========================================================================
    # MFA Support
    # ========================================================================

    @staticmethod
    def generate_mfa_secret() -> str:
        """Generate a new TOTP secret for MFA."""
        return pyotp.random_base32()

    @staticmethod
    def get_mfa_qr_url(username: str, secret: str) -> str:
        """Get QR code URL for MFA setup."""
        return pyotp.totp.TOTP(secret).provisioning_uri(name=username, issuer_name="APGI API")

    @staticmethod
    def verify_mfa_code(secret: str, code: str) -> bool:
        """Verify a TOTP code."""
        totp = pyotp.TOTP(secret)
        return totp.verify(code)

    # ========================================================================
    # Token Creation
    # ========================================================================

    def create_access_token(self, user_id: str, username: str, roles: List[str]) -> str:
        """
        Create a JWT access token.

        Args:
            user_id: User identifier
            username: Username
            roles: List of user roles

        Returns:
            Encoded JWT token string
        """
        import uuid
        from datetime import timezone

        expires_at = datetime.now(timezone.utc) + timedelta(
            minutes=self.access_token_expire_minutes
        )

        # Generate unique JTI for token revocation
        jti = str(uuid.uuid4())

        payload = TokenPayload(
            user_id=user_id,
            username=username,
            roles=roles,
            exp=expires_at,
            token_type="access",  # nosec: B106 - token type, not password
            jti=jti,
        )

        # Add kid (Key ID) to header for provenance tracking
        # In production, this would be a hash of the current key or a version identifier
        headers = {"kid": f"apgi-{settings.environment}-v1"}

        token = jwt.encode(
            payload.to_dict(),
            cast(str, self.secret_key),
            algorithm=self.algorithm,
            headers=headers,
        )

        return token

    def create_refresh_token(
        self, user_id: str, username: str, roles: List[str], remember_me: bool = False
    ) -> str:
        """
        Create a JWT refresh token.

        Args:
            user_id: User identifier
            username: Username
            roles: List of user roles
            remember_me: If True, extend expiry to 30 days

        Returns:
            Encoded JWT refresh token string
        """
        from datetime import timezone

        expire_days = 30 if remember_me else self.refresh_token_expire_days
        expires_at = datetime.now(timezone.utc) + timedelta(days=expire_days)

        payload = TokenPayload(
            user_id=user_id,
            username=username,
            roles=roles,
            exp=expires_at,
            token_type="refresh",  # nosec: B106 - token type, not password
        )

        token = jwt.encode(payload.to_dict(), cast(str, self.secret_key), algorithm=self.algorithm)

        return token

    # ========================================================================
    # Token Verification
    # ========================================================================

    async def verify_token(self, token: str, expected_type: str = "access") -> TokenPayload:
        """
        Verify and decode a JWT token.

        Args:
            token: JWT token string
            expected_type: Expected token type ("access" or "refresh")

        Returns:
            TokenPayload with decoded token data

        Raises:
            InvalidTokenError: If token is invalid or malformed
            ExpiredTokenError: If token has expired
        """
        try:
            # Decode token
            payload_dict = jwt.decode(
                token,
                cast(str, self.secret_key),
                algorithms=[self.algorithm],
            )

            # Parse payload
            payload = TokenPayload.from_dict(payload_dict)

            # Verify token type
            if payload.token_type != expected_type:
                raise InvalidTokenError(
                    f"Invalid token type: expected {expected_type}, got {payload.token_type}"
                )

            # Check expiration (jwt.decode already checks this, but we handle it explicitly)
            from datetime import timezone

            if datetime.now(timezone.utc) > payload.exp:
                raise ExpiredTokenError("Token has expired")

            # Check if token has been revoked (only for access tokens with JTI)
            if payload.jti and self.redis and expected_type == "access":  # pragma: no branch
                if await self.redis.exists(
                    f"revoked_access_tokens:{payload.jti}"
                ):  # pragma: no branch
                    raise InvalidTokenError("Token has been revoked")

            return payload

        except jwt.ExpiredSignatureError:
            raise ExpiredTokenError("Token has expired")
        except ExpiredTokenError:
            # Re-raise our own ExpiredTokenError
            raise
        except jwt.InvalidTokenError as e:
            raise InvalidTokenError(f"Invalid token: {str(e)}")
        except Exception as e:
            raise InvalidTokenError(f"Token verification failed: {str(e)}")

    async def revoke_access_token(self, access_token: str) -> bool:
        """
        Revoke an access token by adding its JTI to the blocklist.

        Args:
            access_token: Access token to revoke

        Returns:
            True if token was revoked, False if invalid or already revoked

        Raises:
            InvalidTokenError: If token is malformed
        """
        if not self.redis:
            logger.warning("Redis not available, cannot revoke access token")
            return False

        try:
            # Verify token to get JTI (but don't check revocation yet)
            payload_dict = jwt.decode(
                access_token,
                cast(str, self.secret_key),
                algorithms=[self.algorithm],
                options={"verify_exp": False},
            )

            jti = payload_dict.get("jti")
            exp = payload_dict.get("exp")

            if not jti:
                logger.info(
                    "Access token has no JTI, logout successful (token will expire naturally)"
                )
                return True

            # Add to blocklist with expiry
            from datetime import timezone

            expires_at = datetime.fromtimestamp(exp, tz=timezone.utc) if exp else None
            ttl = None
            if expires_at:
                now = datetime.now(timezone.utc)
                if expires_at > now:
                    ttl = int((expires_at - now).total_seconds())

            key = f"revoked_access_tokens:{jti}"
            await self.redis.setex(key, ttl or 3600, "1")  # Default 1 hour if no expiry

            logger.info(f"Access token with JTI {jti} revoked")
            return True

        except jwt.InvalidTokenError as e:
            raise InvalidTokenError(f"Invalid token: {str(e)}")
        except Exception as e:
            logger.error(f"Failed to revoke access token: {str(e)}")
            return False

    # ========================================================================
    # User Authentication
    # ========================================================================

    def authenticate_user(
        self, username: str, password: str, mfa_code: Optional[str] = None
    ) -> Optional[User]:
        """
        Authenticate a user with username and password.

        Args:
            username: Username
            password: Plain text password
            mfa_code: MFA verification code if required

        Returns:
            User object if authentication successful, None otherwise
        """
        # Look up user
        user = self.db.query(User).filter(User.username == username).first()

        if not user:
            return None

        # Check if account is locked
        from datetime import timezone

        now = datetime.now(timezone.utc)
        if user.locked_until and now < user.locked_until:
            raise AuthenticationError("Account is locked due to too many failed login attempts")

        # Check if account is active (email verified)
        if not user.is_active:
            raise AuthenticationError("Account is pending activation")

        # Verify password
        if not self.verify_password(password, user.password_hash):  # type: ignore[arg-type]
            # Increment failed login attempts
            user.failed_login_attempts = (user.failed_login_attempts or 0) + 1  # type: ignore[assignment]
            # Check if should lock account (5 attempts)
            if user.failed_login_attempts >= 5:
                user.locked_until = now + timedelta(minutes=15)  # type: ignore[assignment]
                logger.warning(
                    f"Account locked for user {username} due to too many failed attempts"
                )
            try:
                self.db.commit()
            except Exception as e:
                self.db.rollback()
                logger.error(f"Failed to update failed login attempts for user {username}: {e}")
            return None

        # Verify MFA if enabled
        if user.mfa_enabled:  # pragma: no branch
            if not mfa_code:
                raise AuthenticationError("MFA code required")
            if not self.verify_mfa_code(user.mfa_secret, mfa_code):  # type: ignore[arg-type]  # type: ignore[attr-defined]  # pragma: no branch
                # Increment failed login attempts for MFA failures
                user.failed_login_attempts = (user.failed_login_attempts or 0) + 1  # type: ignore[assignment]
                # Check if should lock account (5 attempts)
                if user.failed_login_attempts >= 5:
                    user.locked_until = now + timedelta(minutes=15)  # type: ignore[assignment]
                    logger.warning(
                        f"Account locked for user {username} due to too many failed MFA attempts"
                    )
                try:
                    self.db.commit()
                except Exception as e:
                    self.db.rollback()
                    logger.error(f"Failed to update failed MFA attempts for user {username}: {e}")
                raise AuthenticationError("Invalid MFA code")

        # Successful login: reset failed attempts and update last login
        user.failed_login_attempts = 0  # type: ignore[assignment]
        user.locked_until = None  # type: ignore[assignment]
        user.last_login = now  # type: ignore[assignment]

        try:
            self.db.commit()
        except Exception as e:
            self.db.rollback()
            logger.error(f"Failed to update login info for user {username}: {e}")
            # Still return user since login succeeded, just log the error

        return user

    def create_tokens_for_user(self, user: User, remember_me: bool = False) -> Dict[str, Any]:
        """
        Create access and refresh tokens for a user.

        Args:
            user: User object
            remember_me: Whether to extend refresh token expiry

        Returns:
            Dictionary with access_token, refresh_token, token_type, and expires_in
        """
        access_token = self.create_access_token(
            user_id=user.user_id, username=user.username, roles=user.roles  # type: ignore[arg-type]
        )

        refresh_token = self.create_refresh_token(
            user_id=user.user_id, username=user.username, roles=user.roles  # type: ignore[arg-type]
        )

        # Determine refresh token expiry based on remember_me
        if remember_me:
            refresh_expire_days = 30  # Extended expiry for remember_me
        else:
            refresh_expire_days = self.refresh_token_expire_days

        # Store refresh token in database
        from datetime import timezone

        token_hash = self.hash_password(refresh_token)
        import hashlib

        token_sha256 = hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()
        expires_at = datetime.now(timezone.utc) + timedelta(days=refresh_expire_days)

        try:
            db_refresh_token = RefreshToken(
                user_id=user.user_id,
                token_hash=token_hash,
                token_sha256=token_sha256,
                expires_at=expires_at,
            )
            self.db.add(db_refresh_token)
            self.db.commit()
        except Exception as e:
            self.db.rollback()
            logger.error(f"Failed to create refresh token for user {user.user_id}: {e}")
            raise

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",  # nosec: B105 - token type, not password
            "expires_in": self.access_token_expire_minutes * 60,
            "refresh_expires_in": refresh_expire_days * 24 * 60 * 60,
        }

    # ========================================================================
    # Token Refresh
    # ========================================================================

    @staticmethod
    def constant_time_compare(val1: str, val2: str) -> bool:
        """
        Constant-time comparison to prevent timing attacks.

        Args:
            val1: First value to compare
            val2: Second value to compare

        Returns:
            True if values are equal, False otherwise
        """
        return hmac.compare_digest(val1.encode(), val2.encode())

    async def refresh_access_token(self, refresh_token: str) -> Dict[str, Any]:
        """
        Create a new access token using a refresh token.

        Args:
            refresh_token: Refresh token string

        Returns:
            Dictionary with new access_token, token_type, and expires_in

        Raises:
            InvalidTokenError: If refresh token is invalid
            ExpiredTokenError: If refresh token has expired
            AuthenticationError: If refresh token has been revoked
        """
        # Verify refresh token
        payload = await self.verify_token(refresh_token, expected_type="refresh")

        import hashlib

        sha = hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()
        db_token: Optional[RefreshToken] = (
            self.db.query(RefreshToken)
            .filter(
                and_(
                    RefreshToken.user_id == payload.user_id,
                    RefreshToken.revoked.is_(False),
                    RefreshToken.token_sha256 == sha,
                )
            )
            .first()
        )

        if db_token and not self.verify_password(refresh_token, db_token.token_hash):  # type: ignore[arg-type]
            db_token = None

        if not db_token:
            raise AuthenticationError("Invalid or revoked refresh token")

        # Check expiration in database
        from datetime import timezone

        if datetime.now(timezone.utc) > db_token.expires_at:
            raise ExpiredTokenError("Refresh token has expired")

        # Create new access token
        access_token = self.create_access_token(
            user_id=payload.user_id, username=payload.username, roles=payload.roles
        )

        # Create new refresh token (rotation)
        new_refresh_token = self.create_refresh_token(
            user_id=payload.user_id, username=payload.username, roles=payload.roles
        )

        # Store new refresh token in database
        token_hash = self.hash_password(new_refresh_token)
        import hashlib

        token_sha256 = hashlib.sha256(new_refresh_token.encode("utf-8")).hexdigest()
        expires_at = datetime.now(timezone.utc) + timedelta(days=self.refresh_token_expire_days)

        try:
            new_db_refresh_token = RefreshToken(
                user_id=payload.user_id,
                token_hash=token_hash,
                token_sha256=token_sha256,
                expires_at=expires_at,
            )
            self.db.add(new_db_refresh_token)

            # Revoke only the presented refresh token (not all tokens for the user).
            # Revoking all tokens would silently log out other devices (e.g. mobile, desktop).
            db_token.revoked = True  # type: ignore[assignment]

            self.db.commit()
        except Exception as e:
            self.db.rollback()
            logger.error(f"Failed to rotate refresh token for user {payload.user_id}: {e}")
            raise

        return {
            "access_token": access_token,
            "refresh_token": new_refresh_token,
            "token_type": "bearer",  # nosec: B105 - token type, not password
            "expires_in": self.access_token_expire_minutes * 60,
            "refresh_expires_in": self.refresh_token_expire_days * 24 * 60 * 60,
        }

    # ========================================================================
    # Token Revocation
    # ========================================================================

    async def revoke_refresh_token(self, refresh_token: str) -> bool:
        """
        Revoke a refresh token (logout).

        Args:
            refresh_token: Refresh token to revoke

        Returns:
            True if token was revoked, False if not found
        """
        try:
            # Verify token to get user_id
            payload = await self.verify_token(refresh_token, expected_type="refresh")

            import hashlib

            sha = hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()
            db_token: Optional[RefreshToken] = (
                self.db.query(RefreshToken)
                .filter(
                    and_(
                        RefreshToken.user_id == payload.user_id,
                        RefreshToken.revoked.is_(False),
                        RefreshToken.token_sha256 == sha,
                    )
                )
                .first()
            )

            if db_token and not self.verify_password(refresh_token, db_token.token_hash):  # type: ignore[arg-type]
                db_token = None

            try:
                if db_token:
                    db_token.revoked = True  # type: ignore[assignment]
                    self.db.commit()
                    return True
                return False
            except Exception as e:
                self.db.rollback()
                logger.error(f"Failed to revoke refresh token for user {payload.user_id}: {e}")
                return False

        except (InvalidTokenError, ExpiredTokenError):
            # Token is already invalid, consider it revoked
            return False

    def revoke_all_user_tokens(self, user_id: str) -> int:
        """
        Revoke all refresh tokens for a user.

        Args:
            user_id: User identifier

        Returns:
            Number of tokens revoked
        """
        try:
            tokens = (
                self.db.query(RefreshToken)
                .filter(and_(RefreshToken.user_id == user_id, RefreshToken.revoked.is_(False)))
                .all()
            )
        except Exception as e:
            self.db.rollback()
            logger.error(f"Failed to retrieve tokens for user {user_id}: {e}")
            raise

        count = 0
        try:
            for token in tokens:
                token.revoked = True  # type: ignore[assignment]
                count += 1

            self.db.commit()
            return count
        except Exception as e:
            self.db.rollback()
            logger.error(f"Failed to revoke tokens for user {user_id}: {e}")
            raise

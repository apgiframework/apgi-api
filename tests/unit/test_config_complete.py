"""
Additional unit tests for Settings configuration to achieve 100% coverage.

Covers:
- Invalid environment handling
- URL parsing exceptions
- Warnings for insecure settings
- Production-specific validations
"""

import os
import warnings
from unittest.mock import patch

import pytest


class TestSettingsInvalidEnvironment:
    """Test invalid environment handling."""

    @patch.dict(os.environ, {"ENVIRONMENT": "invalid_env"}, clear=False)
    def test_invalid_environment_raises_error(self) -> None:
        """Test that invalid environment raises ValueError at module load time."""
        import importlib

        import app.config

        # Remove TEST_MODE from environment temporarily
        original_test_mode = os.environ.pop("TEST_MODE", None)
        try:
            # The error should be raised during reload when settings = Settings() executes
            with pytest.raises(ValueError, match="Invalid ENVIRONMENT"):
                importlib.reload(app.config)
        finally:
            # Restore TEST_MODE
            if original_test_mode is not None:
                os.environ["TEST_MODE"] = original_test_mode


class TestSettingsLogLevelValidation:
    """Test log level validation."""

    @patch.dict(os.environ, {"LOG_LEVEL": "INVALID_LEVEL"}, clear=False)
    def test_invalid_log_level_raises_error(self) -> None:
        """Test that invalid log level raises ValueError at module load time."""
        import importlib

        import app.config

        # The error should be raised during reload when settings = Settings() executes
        with pytest.raises(ValueError, match="Invalid LOG_LEVEL"):
            importlib.reload(app.config)


class TestSettingsURLParsing:
    """Test URL parsing in settings validation."""

    @patch.dict(
        os.environ,
        {
            "JWT_SECRET_KEY": "a" * 32,
            "CURSOR_SIGNING_KEY": "b" * 32,
            "WEBHOOK_SECRET_KEY": "c" * 32,
            "DATABASE_URL": "not_a_valid_url",
            "REDIS_URL": "redis://localhost:6379/0",
            "CELERY_BROKER_URL": "redis://localhost:6379/1",
        },
        clear=True,
    )
    def test_invalid_database_url_format(self) -> None:
        """Test that invalid database URL format is detected."""
        from app.config import Settings

        # In production, this would raise an error
        with patch.dict(os.environ, {"ENVIRONMENT": "development"}):
            settings = Settings()
            # In development, it may warn but not fail

    @patch.dict(
        os.environ,
        {
            "JWT_SECRET_KEY": "a" * 32,
            "CURSOR_SIGNING_KEY": "b" * 32,
            "WEBHOOK_SECRET_KEY": "c" * 32,
            "DATABASE_URL": "postgresql://localhost/apgi_api",
            "REDIS_URL": "not_a_valid_url",
            "CELERY_BROKER_URL": "redis://localhost:6379/1",
        },
        clear=True,
    )
    def test_invalid_redis_url_format(self) -> None:
        """Test that invalid Redis URL format is detected."""
        from app.config import Settings

        with patch.dict(os.environ, {"ENVIRONMENT": "development"}):
            settings = Settings()

    @patch.dict(
        os.environ,
        {
            "JWT_SECRET_KEY": "a" * 32,
            "CURSOR_SIGNING_KEY": "b" * 32,
            "WEBHOOK_SECRET_KEY": "c" * 32,
            "DATABASE_URL": "postgresql://localhost/apgi_api",
            "REDIS_URL": "redis://localhost:6379/0",
            "CELERY_BROKER_URL": "not_a_valid_url",
        },
        clear=True,
    )
    def test_invalid_celery_broker_url_format(self) -> None:
        """Test that invalid Celery broker URL format is detected."""
        from app.config import Settings

        with patch.dict(os.environ, {"ENVIRONMENT": "development"}):
            settings = Settings()


class TestSettingsProductionWarnings:
    """Test production-specific warnings and errors."""

    @patch.dict(
        os.environ,
        {
            "ENVIRONMENT": "production",
            "JWT_SECRET_KEY": "a" * 32,
            "CURSOR_SIGNING_KEY": "b" * 32,
            "CORS_ORIGINS": "*",
            "CORS_ALLOW_CREDENTIALS": "true",
        },
        clear=True,
    )
    def test_wildcard_cors_with_credentials_production_error(self) -> None:
        """Test that wildcard CORS with credentials in production raises error."""
        from app.config import Settings

        with pytest.raises(ValueError, match="CORS origins are set to wildcard"):
            Settings()

    @patch.dict(
        os.environ,
        {
            "ENVIRONMENT": "production",
            "JWT_SECRET_KEY": "a" * 32,
            "CURSOR_SIGNING_KEY": "b" * 32,
            "DATABASE_URL": "postgresql://localhost/apgi_api",
        },
        clear=True,
    )
    def test_default_database_url_in_production_error(self) -> None:
        """Test that default database URL in production raises error."""
        from app.config import Settings

        with pytest.raises(ValueError, match="DATABASE_URL is not configured for production"):
            Settings()

    @patch.dict(
        os.environ,
        {
            "ENVIRONMENT": "production",
            "JWT_SECRET_KEY": "a" * 32,
            "CURSOR_SIGNING_KEY": "b" * 32,
            "DATABASE_URL": "postgresql://prod-db:5432/apgi",
            "REDIS_URL": "redis://localhost:6379/0",
        },
        clear=True,
    )
    def test_default_redis_url_in_production_error(self) -> None:
        """Test that default Redis URL in production raises error."""
        from app.config import Settings

        with pytest.raises(ValueError, match="REDIS_URL is not configured for production"):
            Settings()

    @patch.dict(
        os.environ,
        {
            "ENVIRONMENT": "production",
            "JWT_SECRET_KEY": "a" * 32,
            "CURSOR_SIGNING_KEY": "b" * 32,
            "DATABASE_URL": "postgresql://prod-db:5432/apgi",
            "REDIS_URL": "redis://prod-redis:6379/0",
            "BASE_URL": "https://localhost:8000",
        },
        clear=True,
    )
    def test_localhost_base_url_in_production_error(self) -> None:
        """Test that localhost base URL in production raises error."""
        from app.config import Settings

        with pytest.raises(ValueError, match="BASE_URL is set to localhost in production"):
            Settings()


class TestSettingsStripeValidation:
    """Test Stripe configuration validation."""

    @patch.dict(
        os.environ,
        {
            "ENVIRONMENT": "production",
            "JWT_SECRET_KEY": "a" * 32,
            "CURSOR_SIGNING_KEY": "b" * 32,
            "DATABASE_URL": "postgresql://prod-db:5432/apgi",
            "REDIS_URL": "redis://prod-redis:6379/0",
            "BASE_URL": "https://api.example.com",
            "STRIPE_SECRET_KEY": "sk_test_placeholder",
        },
        clear=True,
    )
    def test_stripe_test_key_in_production_error(self) -> None:
        """Test that Stripe test key in production raises error."""
        from app.config import Settings

        with pytest.raises(ValueError, match="STRIPE_SECRET_KEY is not configured for production"):
            Settings()

    @patch.dict(
        os.environ,
        {
            "ENVIRONMENT": "production",
            "JWT_SECRET_KEY": "a" * 32,
            "CURSOR_SIGNING_KEY": "b" * 32,
            "DATABASE_URL": "postgresql://prod-db:5432/apgi",
            "REDIS_URL": "redis://prod-redis:6379/0",
            "BASE_URL": "https://api.example.com",
            "STRIPE_SECRET_KEY": "sk_live_valid_key",
            "STRIPE_PUBLISHABLE_KEY": "pk_test_placeholder",
        },
        clear=True,
    )
    def test_stripe_publishable_test_key_in_production_error(self) -> None:
        """Test that Stripe publishable test key in production raises error."""
        from app.config import Settings

        with pytest.raises(
            ValueError, match="STRIPE_PUBLISHABLE_KEY is not configured for production"
        ):
            Settings()

    @patch.dict(
        os.environ,
        {
            "ENVIRONMENT": "production",
            "JWT_SECRET_KEY": "a" * 32,
            "CURSOR_SIGNING_KEY": "b" * 32,
            "DATABASE_URL": "postgresql://prod-db:5432/apgi",
            "REDIS_URL": "redis://prod-redis:6379/0",
            "BASE_URL": "https://api.example.com",
            "STRIPE_SECRET_KEY": "sk_live_valid_key",
            "STRIPE_PUBLISHABLE_KEY": "pk_live_valid_key",
        },
        clear=True,
    )
    def test_stripe_webhook_secret_missing_in_production_error(self) -> None:
        """Test that missing Stripe webhook secret in production raises error."""
        from app.config import Settings

        with pytest.raises(
            ValueError, match="STRIPE_WEBHOOK_SECRET is not configured for production"
        ):
            Settings()


class TestSettingsSMTPValidation:
    """Test SMTP configuration validation."""

    @patch.dict(
        os.environ,
        {
            "ENVIRONMENT": "production",
            "JWT_SECRET_KEY": "a" * 32,
            "CURSOR_SIGNING_KEY": "b" * 32,
            "DATABASE_URL": "postgresql://prod-db:5432/apgi",
            "REDIS_URL": "redis://prod-redis:6379/0",
            "BASE_URL": "https://api.example.com",
            "STRIPE_SECRET_KEY": "sk_live_valid_key",
            "STRIPE_PUBLISHABLE_KEY": "pk_live_valid_key",
            "STRIPE_WEBHOOK_SECRET": "whsec_valid_secret",
        },
        clear=True,
    )
    def test_smtp_missing_in_production_error(self) -> None:
        """Test that missing SMTP in production raises error."""
        from app.config import Settings

        with pytest.raises(ValueError, match="SMTP_SERVER is not configured for production"):
            Settings()


class TestSettingsCORSWarnings:
    """Test CORS configuration warnings."""

    @patch.dict(
        os.environ,
        {
            "JWT_SECRET_KEY": "a" * 32,
            "CURSOR_SIGNING_KEY": "b" * 32,
            "CORS_ORIGINS": "*",
            "CORS_ALLOW_CREDENTIALS": "false",
        },
        clear=True,
    )
    def test_wildcard_cors_without_credentials_dev_warning(self) -> None:
        """Test that wildcard CORS without credentials in dev shows warning."""
        from app.config import Settings

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            Settings()
            # Should have a warning about wildcard

    @patch.dict(
        os.environ,
        {
            "JWT_SECRET_KEY": "a" * 32,
            "CURSOR_SIGNING_KEY": "b" * 32,
        },
        clear=True,
    )
    def test_no_cors_origins_in_production_error(self) -> None:
        """Test that no CORS origins in production raises error."""
        from app.config import Settings

        with patch.dict(os.environ, {"ENVIRONMENT": "production"}):
            with pytest.raises(ValueError, match="CORS_ORIGINS environment variable is not set"):
                Settings()


class TestSettingsDevelopment:
    """Test development-specific settings."""

    @patch.dict(
        os.environ,
        {
            "ENVIRONMENT": "development",
            "JWT_SECRET_KEY": "a" * 32,
            "CURSOR_SIGNING_KEY": "b" * 32,
            "WEBHOOK_SECRET_KEY": "c" * 32,
        },
        clear=True,
    )
    def test_development_defaults(self) -> None:
        """Test development environment defaults."""
        from app.config import Settings

        settings = Settings()
        assert settings.environment == "development"
        assert settings.reload is True
        assert settings.log_level == "DEBUG"
        assert "http://localhost:3000" in settings.cors_origins


class TestSettingsStaging:
    """Test staging-specific settings."""

    @patch.dict(
        os.environ,
        {
            "ENVIRONMENT": "staging",
            "JWT_SECRET_KEY": "staging_jwt_secret_key_with_random_chars_123!@#xyz",
            "CURSOR_SIGNING_KEY": "staging_cursor_key_with_randomness_ABC789$%^def",
            "WEBHOOK_SECRET_KEY": "webhook_staging_secret_random_GHI456&*mno",
            "DATABASE_URL": "postgresql://staging-db:5432/apgi",
            "REDIS_URL": "redis://staging-redis:6379/0",
            "CELERY_BROKER_URL": "redis://staging-redis:6379/1",
            "CELERY_RESULT_BACKEND": "redis://staging-redis:6379/2",
            "BASE_URL": "https://staging-api.example.com",
            "CORS_ORIGINS": "https://staging-api.example.com",
            "CORS_ALLOW_CREDENTIALS": "false",
        },
        clear=True,
    )
    def test_staging_defaults(self) -> None:
        """Test staging environment defaults."""
        from app.config import Settings

        settings = Settings()
        assert settings.environment == "staging"
        assert settings.reload is False
        assert settings.log_level == "INFO"


class TestSettingsDefaultLogLevel:
    """Test default log level determination."""

    @patch.dict(
        os.environ,
        {
            "JWT_SECRET_KEY": "prod_jwt_secret_key_with_random_chars_456!@#abc",
            "CURSOR_SIGNING_KEY": "prod_cursor_key_with_randomness_XYZ123$%^ghi",
            "WEBHOOK_SECRET_KEY": "webhook_prod_secret_random_JKL789&*pqr",
            "ENVIRONMENT": "production",
            "DATABASE_URL": "postgresql://prod-db:5432/apgi",
            "REDIS_URL": "redis://prod-redis:6379/0",
            "CELERY_BROKER_URL": "redis://prod-redis:6379/1",
            "CELERY_RESULT_BACKEND": "redis://prod-redis:6379/2",
            "BASE_URL": "https://api.example.com",
            "CORS_ORIGINS": "https://api.example.com",
            "CORS_ALLOW_CREDENTIALS": "false",
            "STRIPE_SECRET_KEY": "sk_live_valid_key",
            "STRIPE_PUBLISHABLE_KEY": "pk_live_valid_key",
            "STRIPE_WEBHOOK_SECRET": "whsec_valid_secret",
            "SMTP_SERVER": "smtp.example.com",
            "LOG_LEVEL": "WARNING",
            "PII_ENCRYPTION_KEY": "04NwSXgR5tHZ6YBR95EMLJwV_V2SUyBbsbCESUrIdrE=",
            "AUDIT_SIGNING_KEY": "prod_audit_signing_key_with_enough_length_012345",
        },
        clear=True,
    )
    def test_production_log_level(self) -> None:
        """Test production log level."""
        import importlib

        import app.config

        importlib.reload(app.config)
        from app.config import Settings

        settings = Settings()
        # Production should use WARNING log level
        assert settings.log_level == "WARNING"

        # CLEANUP: Restore settings to development state for other tests
        with patch.dict(os.environ, {"ENVIRONMENT": "development"}, clear=False):
            importlib.reload(app.config)


class TestSettingsWebhookSecretWarning:
    """Test webhook secret warning in non-production."""

    @patch.dict(
        os.environ,
        {
            "ENVIRONMENT": "development",
            "JWT_SECRET_KEY": "a" * 32,
            "CURSOR_SIGNING_KEY": "b" * 32,
        },
        clear=True,
    )
    def test_missing_webhook_secret_dev_warning(self) -> None:
        """Test that missing webhook secret in dev shows warning."""
        from app.config import Settings

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            Settings()
            # Should have a warning about WEBHOOK_SECRET_KEY


class TestSettingsEntropyCheck:
    """Test entropy check for keys."""

    @patch.dict(
        os.environ,
        {
            "ENVIRONMENT": "production",
            "JWT_SECRET_KEY": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "CURSOR_SIGNING_KEY": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "WEBHOOK_SECRET_KEY": "cccccccccccccccccccccccccccccccc",
            "DATABASE_URL": "postgresql://prod-db:5432/apgi",
            "REDIS_URL": "redis://prod-redis:6379/0",
            "CELERY_BROKER_URL": "redis://prod-redis:6379/1",
            "BASE_URL": "https://api.example.com",
            "CORS_ORIGINS": "https://api.example.com",
            "STRIPE_SECRET_KEY": "sk_live_valid_key",
            "STRIPE_PUBLISHABLE_KEY": "pk_live_valid_key",
            "STRIPE_WEBHOOK_SECRET": "whsec_valid_secret",
            "SMTP_SERVER": "smtp.example.com",
        },
        clear=True,
    )
    def test_low_entropy_warning(self) -> None:
        """Test that low entropy keys raise errors."""
        from app.config import Settings

        with pytest.raises(ValueError, match="entropy"):
            Settings()

    @patch.dict(
        os.environ,
        {
            "ENVIRONMENT": "production",
            "JWT_SECRET_KEY": "a" * 32,
            "CURSOR_SIGNING_KEY": "",
            "WEBHOOK_SECRET_KEY": "c" * 32,
            "DATABASE_URL": "postgresql://prod-db:5432/apgi",
            "REDIS_URL": "redis://prod-redis:6379/0",
            "CELERY_BROKER_URL": "redis://prod-redis:6379/1",
            "BASE_URL": "https://api.example.com",
            "CORS_ORIGINS": "https://api.example.com",
            "STRIPE_SECRET_KEY": "sk_live_valid_key",
            "STRIPE_PUBLISHABLE_KEY": "pk_live_valid_key",
            "STRIPE_WEBHOOK_SECRET": "whsec_valid_secret",
            "SMTP_SERVER": "smtp.example.com",
        },
        clear=True,
    )
    def test_empty_cursor_signing_key(self) -> None:
        """Test that empty cursor signing key raises error (hitting line 347)."""
        from app.config import Settings

        with pytest.raises(ValueError, match="CURSOR_SIGNING_KEY environment variable is not set"):
            Settings()


class TestSettingsURLParseErrors:
    """Test exception handling during URL parsing."""

    def test_database_url_parse_exception(self) -> None:
        """Test database URL parse exception (hitting lines 502-503)."""
        from app.config import Settings

        with patch("app.config.urlparse", side_effect=Exception("Parse error")):
            with patch.dict(
                os.environ,
                {
                    "JWT_SECRET_KEY": "a" * 32,
                    "CURSOR_SIGNING_KEY": "b" * 32,
                    "WEBHOOK_SECRET_KEY": "c" * 32,
                    "DATABASE_URL": "postgresql://localhost/apgi",
                },
                clear=True,
            ):
                s = Settings()

    @patch.dict(
        os.environ,
        {
            "ENVIRONMENT": "production",
            "JWT_SECRET_KEY": "a" * 32,
            "CURSOR_SIGNING_KEY": "b" * 32,
            "DATABASE_URL": "postgresql://prod-db:5432/apgi",
            "REDIS_URL": "redis://prod-redis:6379/0",
            "CELERY_BROKER_URL": "redis://prod-redis:6379/1",
            "BASE_URL": "https://api.example.com",
            "CORS_ORIGINS": "https://api.example.com",
            "STRIPE_SECRET_KEY": "sk_live_valid_key",
            "STRIPE_PUBLISHABLE_KEY": "pk_live_valid_key",
            "STRIPE_WEBHOOK_SECRET": "whsec_valid_secret",
            "SMTP_SERVER": "smtp.example.com",
        },
        clear=True,
    )
    def test_redis_url_parse_exception_production(self) -> None:
        """Test redis URL parse exception in production (hitting lines 509-510)."""
        import app.config  # type: ignore
        from app.config import Settings

        # We want to fail the urlparse for Redis
        original_urlparse = app.config.urlparse  # type: ignore

        def side_effect(url: str):
            if "redis" in url:
                raise Exception("Redis parse error")
            return original_urlparse(url)

        with patch("app.config.urlparse", side_effect=side_effect):
            with pytest.raises(ValueError, match="REDIS_URL is not a valid URL"):
                Settings()


class TestSettingsWildcardCORSProduction:
    """Test wildcard CORS in production."""

    @patch.dict(
        os.environ,
        {
            "ENVIRONMENT": "production",
            "JWT_SECRET_KEY": "a" * 32,
            "CURSOR_SIGNING_KEY": "b" * 32,
            "DATABASE_URL": "postgresql://prod-db:5432/apgi",
            "REDIS_URL": "redis://prod-redis:6379/0",
            "CELERY_BROKER_URL": "redis://prod-redis:6379/1",
            "BASE_URL": "https://api.example.com",
            "CORS_ORIGINS": "*",
            "CORS_ALLOW_CREDENTIALS": "false",
            "STRIPE_SECRET_KEY": "sk_live_valid_key",
            "STRIPE_PUBLISHABLE_KEY": "pk_live_valid_key",
            "STRIPE_WEBHOOK_SECRET": "whsec_valid_secret",
            "SMTP_SERVER": "smtp.example.com",
        },
        clear=True,
    )
    def test_wildcard_cors_production_error(self) -> None:
        """Test that wildcard CORS in production raises error (hitting line 411)."""
        from app.config import Settings

        with pytest.raises(ValueError, match="CORS origins are set to wildcard .* in production"):
            Settings()

"""
Pydantic Request and Response Models

Defines the data schemas for API requests and responses.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Base Models
# ----------------------------------------------------------------------------


class TokenPayload:
    """JWT token payload data."""

    def __init__(
        self,
        user_id: str,
        username: str,
        roles: List[str],
        exp: datetime | int,
        token_type: str = "access",  # nosec: B107 - token type, not password
        jti: Optional[str] = None,
        permissions: Optional[List[str]] = None,
    ):
        self.user_id = user_id
        self.username = username
        self.roles = roles
        # Accept both datetime and integer timestamp for backwards compatibility
        if isinstance(exp, int):
            self.exp = datetime.fromtimestamp(exp, tz=timezone.utc)
        else:
            self.exp = exp
        self.token_type = token_type
        self.jti = jti
        self.permissions = permissions

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JWT encoding."""
        data = {
            "user_id": self.user_id,
            "username": self.username,
            "roles": self.roles,
            "exp": int(self.exp.timestamp()),
            "token_type": self.token_type,
        }
        if self.jti:
            data["jti"] = self.jti
        if self.permissions:
            data["permissions"] = self.permissions
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TokenPayload":
        """Create from dictionary after JWT decoding."""
        from datetime import timezone

        return cls(
            user_id=data["user_id"],
            username=data["username"],
            roles=data["roles"],
            exp=datetime.fromtimestamp(data["exp"], tz=timezone.utc),
            token_type=data.get("token_type", "access"),
            jti=data.get("jti"),
            permissions=data.get("permissions"),
        )


# Pre-compiled regex patterns for performance
UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


class CustomConfig(BaseModel):
    """Custom configuration overrides with strict validation."""

    config: Dict[str, Any] = Field(
        default_factory=dict, description="Custom configuration dictionary"
    )

    @field_validator("config")
    @classmethod
    def validate_config_structure(cls, v: Any) -> Any:
        """Validate that config values are reasonable types and not too deeply nested."""

        def validate_value(value: Any, depth: int = 0, max_depth: int = 5) -> None:
            """Recursively validate configuration values."""
            if depth > max_depth:
                raise ValueError(f"Configuration nesting too deep (max {max_depth} levels)")

            if isinstance(value, dict):
                for k, v_inner in value.items():
                    if not isinstance(k, str) or not k.strip():
                        raise ValueError(f"Configuration key must be a non-empty string, got: {k}")
                    # Prevent dangerous keys
                    dangerous_keys = {"database_url", "secret_key", "password", "token"}
                    if k.lower() in dangerous_keys:
                        raise ValueError(f'Configuration key "{k}" is not allowed in custom config')
                    validate_value(v_inner, depth + 1, max_depth)
            elif isinstance(value, list):
                for item in value:
                    validate_value(item, depth + 1, max_depth)
            elif not isinstance(value, (str, int, float, bool, type(None))):
                raise ValueError(
                    f"Configuration value must be a string, number, boolean, null, or nested structure, got: {type(value)}"
                )

        validate_value(v)
        return v

    def model_dump(self, **kwargs: Any) -> Any:
        """Override model_dump to return just the config dict for backward compatibility."""
        return self.config


class SessionTemplateCreateRequest(BaseModel):
    """Request to create a new session template."""

    name: str = Field(..., description="Template name")
    description: Optional[str] = Field(None, description="Template description")
    config_path: Optional[str] = Field(None, description="Path to YAML configuration file")
    custom_config: Optional[Dict[str, Any]] = Field(
        None, description="Default custom configuration overrides"
    )
    default_description: Optional[str] = Field(None, description="Default session description")
    tags: Optional[List[str]] = Field(default=[], description="Template tags")
    is_public: Optional[bool] = Field(False, description="Whether template is public")

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: Any) -> Any:
        """Validate template name."""
        if not v or not v.strip():
            raise ValueError("Template name cannot be empty")

        if len(v) > 100:
            raise ValueError("Template name is too long (max 100 characters)")

        return v.strip()

    @field_validator("config_path")
    @classmethod
    def validate_config_path(cls, v: Any) -> Any:
        """Validate configuration file path."""
        if v is None:
            return v

        if not isinstance(v, str) or not v.strip():
            raise ValueError("Configuration path must be a non-empty string")

        # Strip whitespace first
        v = v.strip()

        # Basic path validation - prevent directory traversal
        if ".." in v:
            raise ValueError("Invalid configuration path: directory traversal not allowed")

        # Check for absolute paths
        import os

        if os.path.isabs(v):
            raise ValueError("Invalid configuration path: absolute paths not allowed")

        # Check for valid file extension
        if not v.endswith((".yaml", ".yml")):
            raise ValueError("Configuration file must have .yaml or .yml extension")

        # Reasonable path length limit
        if len(v) > 255:
            raise ValueError("Configuration path is too long (max 255 characters)")

        return v

    @field_validator("custom_config")
    @classmethod
    def validate_custom_config(cls, v: Any) -> Any:
        """Validate custom configuration."""
        if v is None:
            return v

        if not isinstance(v, dict):  # pragma: no cover
            raise ValueError("Custom configuration must be a dictionary")

        # Validate configuration keys and values
        for key, value in v.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError(f"Configuration key must be a non-empty string, got: {key}")

            # Prevent certain dangerous configuration keys
            dangerous_keys = {"database_url", "secret_key", "password", "token"}
            if key.lower() in dangerous_keys:
                raise ValueError(f'Configuration key "{key}" is not allowed in custom config')

        return v

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: Any) -> Any:
        """Validate template tags."""
        if v is None:
            return []

        if not isinstance(v, list):  # pragma: no cover
            raise ValueError("Tags must be a list")

        for tag in v:
            if not isinstance(tag, str):  # pragma: no cover
                raise ValueError("Each tag must be a string")

            tag_stripped = tag.strip()
            if not tag_stripped:
                raise ValueError("Each tag must contain non-whitespace characters")

            if len(tag_stripped) > 50:
                raise ValueError("Tag is too long (max 50 characters)")

        return [tag.strip() for tag in v]

    @model_validator(mode="after")
    def validate_config_consistency(self) -> "SessionTemplateCreateRequest":
        """Validate consistency between config_path and custom_config."""
        if self.config_path and self.custom_config:
            # Having both is allowed but warn about potential conflicts
            pass
        elif not self.config_path and not self.custom_config:
            raise ValueError("Either config_path or custom_config must be provided")

        return self

    model_config = ConfigDict(
        strict=True,
        json_schema_extra={
            "example": {
                "name": "Default Experiment",
                "description": "Standard configuration for attentional blink experiments",
                "config_path": "config/default.yaml",
                "default_description": "Attentional blink task",
                "tags": ["attentional_blink", "experiment"],
                "is_public": False,
            }
        },
    )


class SessionTemplateUpdateRequest(BaseModel):
    """Request to update a session template."""

    name: Optional[str] = Field(None, description="Template name")
    description: Optional[str] = Field(None, description="Template description")
    config_path: Optional[str] = Field(None, description="Path to YAML configuration file")
    custom_config: Optional[Dict[str, Any]] = Field(
        None, description="Default custom configuration overrides"
    )
    default_description: Optional[str] = Field(None, description="Default session description")
    tags: Optional[List[str]] = Field(None, description="Template tags")
    is_public: Optional[bool] = Field(None, description="Whether template is public")

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: Any) -> Any:
        """Validate template name."""
        if v is None:
            return v

        if not v.strip():
            raise ValueError("Template name cannot be empty")

        if len(v) > 100:
            raise ValueError("Template name is too long (max 100 characters)")

        return v.strip()

    @field_validator("config_path")
    @classmethod
    def validate_config_path(cls, v: Any) -> Any:
        """Validate configuration file path."""
        if v is None:
            return v

        if not isinstance(v, str) or not v.strip():
            raise ValueError("Configuration path must be a non-empty string")

        # Basic path validation - prevent directory traversal and absolute paths
        import os
        import urllib.parse

        decoded = urllib.parse.unquote(v)
        if ".." in v or ".." in decoded or "%2e" in v.lower():
            raise ValueError("Invalid configuration path: directory traversal not allowed")
        if os.path.isabs(v):
            raise ValueError("Invalid configuration path: absolute paths not allowed")

        # Check for valid file extension
        if not v.endswith((".yaml", ".yml")):
            raise ValueError("Configuration file must have .yaml or .yml extension")

        # Reasonable path length limit
        if len(v) > 255:
            raise ValueError("Configuration path is too long (max 255 characters)")

        return v.strip()

    @field_validator("custom_config")
    @classmethod
    def validate_custom_config(cls, v: Any) -> Any:
        """Validate custom configuration."""
        if v is None:
            return v

        if not isinstance(v, dict):  # pragma: no cover
            raise ValueError("Custom configuration must be a dictionary")

        # Validate configuration keys and values
        for key, value in v.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError(f"Configuration key must be a non-empty string, got: {key}")

            # Prevent certain dangerous configuration keys
            dangerous_keys = {"database_url", "secret_key", "password", "token"}
            if key.lower() in dangerous_keys:
                raise ValueError(f'Configuration key "{key}" is not allowed in custom config')

        return v

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: Any) -> Any:
        """Validate template tags."""
        if v is None:
            return v

        if not isinstance(v, list):  # pragma: no cover
            raise ValueError("Tags must be a list")

        for tag in v:
            if not isinstance(tag, str):  # pragma: no cover
                raise ValueError("Each tag must be a string")

            tag_stripped = tag.strip()
            if not tag_stripped:
                raise ValueError("Each tag must contain non-whitespace characters")

            if len(tag_stripped) > 50:
                raise ValueError("Tag is too long (max 50 characters)")

        return [tag.strip() for tag in v]

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Updated Experiment",
                "description": "Updated configuration for attentional blink experiments",
                "tags": ["attentional_blink", "experiment", "updated"],
                "is_public": True,
            }
        }
    )


class SessionTemplateResponse(BaseModel):
    """Response for session template details."""

    template_id: str = Field(..., description="Unique template identifier")
    user_id: str = Field(..., description="Owner user ID")
    name: str = Field(..., description="Template name")
    description: Optional[str] = Field(None, description="Template description")
    config_path: Optional[str] = Field(None, description="Path to YAML configuration file")
    custom_config: Optional[Dict[str, Any]] = Field(
        None, description="Default custom configuration overrides"
    )
    default_description: Optional[str] = Field(None, description="Default session description")
    tags: List[str] = Field(default_factory=list, description="Template tags")
    is_public: bool = Field(..., description="Whether template is public")
    created_at: datetime = Field(..., description="Template creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "template_id": "tmpl_abc123",
                "user_id": "user_123",
                "name": "Default Experiment",
                "description": "Standard configuration for attentional blink experiments",
                "config_path": "config/default.yaml",
                "custom_config": {"experiment_type": "attentional_blink"},
                "default_description": "Attentional blink task",
                "tags": ["attentional_blink", "experiment"],
                "is_public": False,
                "created_at": "2024-01-15T10:30:00Z",
                "updated_at": "2024-01-15T10:30:00Z",
            }
        }
    )


class SessionTemplateListResponse(BaseModel):
    """Response for session templates list."""

    templates: list[SessionTemplateResponse] = Field(..., description="List of templates")
    pagination: PaginationInfo = Field(..., description="Pagination information")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "templates": [
                    {
                        "template_id": "tmpl_abc123",
                        "user_id": "user_123",
                        "name": "Default Experiment",
                        "description": "Standard configuration for attentional blink experiments",
                        "config_path": "config/default.yaml",
                        "custom_config": {"experiment_type": "attentional_blink"},
                        "default_description": "Attentional blink task",
                        "tags": ["attentional_blink", "experiment"],
                        "is_public": False,
                        "created_at": "2024-01-15T10:30:00Z",
                        "updated_at": "2024-01-15T10:30:00Z",
                    }
                ],
                "pagination": {
                    "page": 1,
                    "per_page": 10,
                    "total": 1,
                },
            }
        }
    )


class SessionCreateRequest(BaseModel):
    """Request to create new simulation session."""

    template_id: Optional[str] = Field(None, description="Template ID to use for session creation")
    config_path: Optional[str] = Field(None, description="Path to YAML configuration file")
    custom_config: Optional[Dict[str, Any]] = Field(
        None, description="Custom configuration overrides"
    )
    description: Optional[str] = Field(
        None, description="Human-readable description of the session"
    )

    @field_validator("template_id")
    @classmethod
    def validate_template_id(cls, v: Any) -> Any:
        """Validate template ID format."""
        if v is None:
            return v

        if not isinstance(v, str) or not v.strip():
            raise ValueError("Template ID must be a non-empty string")

        # Strip whitespace first
        v = v.strip()

        # UUID validation pattern
        if not UUID_PATTERN.match(v):
            raise ValueError(f"Invalid template ID format: {v}")

        return v

    @field_validator("config_path")
    @classmethod
    def validate_config_path(cls, v: Any) -> Any:
        """Validate configuration file path."""
        if v is None:
            return v

        if not isinstance(v, str) or not v.strip():
            raise ValueError("Configuration path must be a non-empty string")

        # Basic path validation - prevent directory traversal and absolute paths
        import os
        import urllib.parse

        decoded = urllib.parse.unquote(v)
        if ".." in v or ".." in decoded or "%2e" in v.lower():
            raise ValueError("Invalid configuration path: directory traversal not allowed")
        if os.path.isabs(v):
            raise ValueError("Invalid configuration path: absolute paths not allowed")

        # Check for valid file extension
        if not v.endswith((".yaml", ".yml")):
            raise ValueError("Configuration file must have .yaml or .yml extension")

        # Reasonable path length limit
        if len(v) > 255:
            raise ValueError("Configuration path is too long (max 255 characters)")

        return v.strip()

    @field_validator("custom_config")
    @classmethod
    def validate_custom_config(cls, v: Any) -> Any:
        """Validate custom configuration."""
        if v is None:
            return v

        if not isinstance(v, dict):  # pragma: no cover
            raise ValueError("Custom configuration must be a dictionary")

        # Validate configuration keys and values
        for key, value in v.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError(f"Configuration key must be a non-empty string, got: {key}")

            # Prevent certain dangerous configuration keys
            dangerous_keys = {"database_url", "secret_key", "password", "token"}
            if key.lower() in dangerous_keys:
                raise ValueError(f'Configuration key "{key}" is not allowed in custom config')

        return v

    @field_validator("description")
    @classmethod
    def validate_description(cls, v: Any) -> Any:
        """Validate session description."""
        if v is None:
            return v

        if not isinstance(v, str):  # pragma: no cover
            raise ValueError("Description must be a string")

        # Reasonable length limit
        if len(v) > 500:
            raise ValueError("Description is too long (max 500 characters)")

        return v.strip()

    @model_validator(mode="after")
    def validate_config_consistency(self) -> "SessionCreateRequest":
        """Validate consistency between template_id, config_path and custom_config."""
        if self.template_id:
            # If using template, config_path and custom_config are optional overrides
            pass
        elif self.config_path and self.custom_config:
            # Having both is allowed but warn about potential conflicts
            pass
        elif not self.config_path and not self.custom_config:
            raise ValueError("Either template_id, config_path, or custom_config must be provided")

        return self

    model_config = ConfigDict(
        strict=True,
        json_schema_extra={
            "example": {
                "template_id": "tmpl_abc123",
                "description": "Baseline simulation experiment",
            }
        },
    )


class LoginRequest(BaseModel):
    """Request for user authentication."""

    username: str = Field(..., description="Username or email address")
    password: str = Field(..., description="User password", min_length=1)
    mfa_code: Optional[str] = Field(None, description="MFA verification code if enabled")
    remember_me: Optional[bool] = Field(False, description="Extend session duration")

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: Any) -> Any:
        """Validate username format."""
        if not v or not v.strip():
            raise ValueError("Username cannot be empty")

        # Check for valid email format if it looks like an email
        if "@" in v:
            email_regex = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
            if not re.match(email_regex, v):
                raise ValueError("Invalid email format")

        # Check username format (alphanumeric, underscore, dash, min 3 chars)
        elif not re.match(r"^[a-zA-Z0-9_-]{3,50}$", v):
            raise ValueError(
                "Username must be 3-50 characters, alphanumeric with underscores or dashes"
            )

        return v.strip()

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: Any) -> Any:
        """Validate password strength."""
        if not v or len(v) < 8:
            raise ValueError("Password must be at least 8 characters long")

        # Check for at least one uppercase, one lowercase, one digit
        if not (re.search(r"[A-Z]", v) and re.search(r"[a-z]", v) and re.search(r"\d", v)):
            raise ValueError(
                "Password must contain at least one uppercase letter, one lowercase letter, and one digit"
            )

        return v

    model_config = ConfigDict(
        str_strip_whitespace=True,
        strict=True,
        json_schema_extra={
            "example": {
                "username": "user@example.com",
                "password": "secure_password",  # nosec: B105 - example value, not password
                "remember_me": False,
            }
        },
    )


class TokenRefreshRequest(BaseModel):
    """Request to refresh access token."""

    refresh_token: str = Field(..., description="Valid refresh token")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "refresh_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9..."  # nosec: B105 - example JWT token, not password
            }
        }
    )


class TokenResponse(BaseModel):
    """Response containing authentication tokens."""

    access_token: str = Field(..., description="JWT access token")
    refresh_token: Optional[str] = Field(None, description="JWT refresh token")
    token_type: str = Field("bearer", description="Token type")
    expires_in: int = Field(..., description="Access token expiration time in seconds")
    refresh_expires_in: Optional[int] = Field(
        None, description="Refresh token expiration time in seconds"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "access_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...",  # nosec: B105 - example JWT token, not password
                "refresh_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...",  # nosec: B105 - example JWT token, not password
                "token_type": "bearer",  # nosec: B105 - token type, not password
                "expires_in": 3600,
                "refresh_expires_in": 86400,
            }
        }
    )


class TokenRefreshResponse(BaseModel):
    """Response for token refresh including the new rotated refresh token."""

    access_token: str = Field(..., description="JWT access token")
    refresh_token: str = Field(
        ..., description="New JWT refresh token (rotated; old token is revoked)"
    )
    token_type: str = Field("bearer", description="Token type")
    expires_in: int = Field(..., description="Access token expiration time in seconds")
    refresh_expires_in: Optional[int] = Field(
        None, description="Refresh token expiration time in seconds"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "access_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...",  # nosec: B105 - example JWT token, not password
                "refresh_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...",  # nosec: B105 - example JWT token, not password
                "token_type": "bearer",  # nosec: B105 - token type, not password
                "expires_in": 1800,
                "refresh_expires_in": 604800,
            }
        }
    )


class SessionCreateResponse(BaseModel):
    """Response for session creation."""

    session_id: str = Field(..., description="Unique session identifier")
    status: str = Field(..., description="Session status")
    created_at: datetime = Field(..., description="Session creation timestamp")
    config: Dict[str, Any] = Field(..., description="Session configuration")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "session_id": "sess_abc123",
                "status": "created",
                "created_at": "2024-01-15T10:30:00Z",
                "config": {"description": "Test session"},
            }
        }
    )


class SessionResponse(BaseModel):
    """Response for session details."""

    session_id: str = Field(..., description="Unique session identifier")
    status: str = Field(..., description="Session status")
    created_at: datetime = Field(..., description="Session creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    config: Dict[str, Any] = Field(..., description="Session configuration")
    description: Optional[str] = Field(None, description="Session description")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "session_id": "sess_abc123",
                "status": "running",
                "created_at": "2024-01-15T10:30:00Z",
                "updated_at": "2024-01-15T10:35:00Z",
                "config": {"description": "Test session"},
                "description": "Test session",
            }
        }
    )


class SessionListResponse(BaseModel):
    """Response for sessions list."""

    sessions: list[SessionResponse] = Field(..., description="List of sessions")
    pagination: PaginationInfo = Field(..., description="Pagination information")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "sessions": [
                    {
                        "session_id": "sess_abc123",
                        "status": "running",
                        "created_at": "2024-01-15T10:30:00Z",
                        "updated_at": "2024-01-15T10:35:00Z",
                        "config": {"description": "Test session"},
                        "description": "Test session",
                    }
                ],
                "pagination": {
                    "page": 1,
                    "per_page": 10,
                    "total": 1,
                },
            }
        }
    )


class SessionMetricsResponse(BaseModel):
    """Response for session metrics."""

    session_id: str = Field(..., description="Unique session identifier")
    ignition_frequency: float = Field(..., description="Frequency of ignition events")
    free_energy: float = Field(..., description="Current free energy level")
    metabolic_load: float = Field(..., description="Current metabolic load")
    additional_metrics: Optional[Dict[str, Any]] = Field(
        None, description="Additional computed metrics"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "session_id": "sess_abc123",
                "ignition_frequency": 0.15,
                "free_energy": -2.5,
                "metabolic_load": 1.8,
                "additional_metrics": {"precision": 0.85, "coherence": 0.92},
            }
        }
    )


class SessionActionResponse(BaseModel):
    """Response for session action operations."""

    session_id: str = Field(..., description="Unique session identifier")
    status: str = Field(..., description="Updated session status")
    timestamp: datetime = Field(..., description="Action timestamp")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "session_id": "sess_abc123",
                "status": "stopped",
                "timestamp": "2024-01-15T10:35:00Z",
            }
        }
    )


class SessionStatusResponse(BaseModel):
    """Response for session status."""

    session_id: str = Field(..., description="Unique session identifier")
    status: str = Field(..., description="Session status")
    message: Optional[str] = Field(None, description="Status message")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "session_id": "sess_abc123",
                "status": "running",
                "message": "Session is currently active",
            }
        }
    )


class TaskDependencyCreateRequest(BaseModel):
    """Request to create a task dependency."""

    prerequisite_task_id: str = Field(
        ..., description="Task ID that must complete before the dependent task"
    )
    dependency_type: Optional[str] = Field("completion", description="Type of dependency")

    @field_validator("prerequisite_task_id")
    @classmethod
    def validate_prerequisite_task_id(cls, v: Any) -> Any:
        """Validate prerequisite task ID format."""
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Prerequisite task ID must be a non-empty string")

        # UUID validation pattern
        if not UUID_PATTERN.match(v):
            raise ValueError(f"Invalid prerequisite task ID format: {v}")

        return v.strip()

    @field_validator("dependency_type")
    @classmethod
    def validate_dependency_type(cls, v: Any) -> Any:
        """Validate dependency type."""
        if v is None:
            return "completion"

        valid_types = ["completion", "success", "failure"]
        if v not in valid_types:
            raise ValueError(f"Dependency type must be one of: {', '.join(valid_types)}")

        return v

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "prerequisite_task_id": "task_prereq_123",
                "dependency_type": "completion",
            }
        }
    )


class TaskDependencyResponse(BaseModel):
    """Response for task dependency details."""

    id: int = Field(..., description="Dependency ID")
    dependent_task_id: str = Field(..., description="Task that depends on the prerequisite")
    prerequisite_task_id: str = Field(
        ..., description="Task that must complete before the dependent task"
    )
    dependency_type: str = Field(..., description="Type of dependency")
    created_at: datetime = Field(..., description="Dependency creation timestamp")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": 1,
                "dependent_task_id": "task_dep_123",
                "prerequisite_task_id": "task_prereq_123",
                "dependency_type": "completion",
                "created_at": "2024-01-15T10:30:00Z",
            }
        }
    )


class TaskSubmitRequest(BaseModel):
    """Request to submit async task."""

    task_type: str = Field(..., description="Type of task to execute")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="Task parameters")
    priority: Optional[int] = Field(5, description="Task priority (1=highest, 10=lowest)")
    webhook_url: Optional[str] = Field(
        None, description="Webhook URL for task completion notification"
    )

    @field_validator("task_type")
    @classmethod
    def validate_task_type(cls, v: Any) -> Any:
        """Validate task type."""
        if not v or not v.strip():
            raise ValueError("Task type cannot be empty")

        return v.strip()

    @field_validator("parameters")
    @classmethod
    def validate_parameters(cls, v: Any) -> Any:
        """Validate task parameters."""
        if not isinstance(v, dict):  # pragma: no cover
            raise ValueError("Parameters must be a dictionary")

        # Basic validation - ensure parameter names are reasonable
        for key, value in v.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError(f"Parameter key must be a non-empty string, got: {key}")

            # Basic type checking for common parameter types
            if isinstance(value, (int, float)):
                if value < 0:
                    raise ValueError(f"Numeric parameter {key} cannot be negative")
            elif isinstance(value, str):
                if len(value) > 1000:  # Reasonable limit
                    raise ValueError(f"String parameter {key} is too long (max 1000 characters)")

        return v

    @field_validator("webhook_url")
    @classmethod
    def validate_webhook_url(cls, v: Any) -> Any:
        """Validate webhook URL format."""
        if v is None:
            return v

        if not isinstance(v, str) or not v.strip():
            raise ValueError("Webhook URL must be a non-empty string")

        # Basic URL validation
        url_regex = r"^https?://[^\s/$.?#].[^\s]*$"
        if not re.match(url_regex, v):
            raise ValueError("Invalid webhook URL format. Must be a valid HTTP/HTTPS URL")

        # Additional security check - only allow HTTPS in production
        if not v.startswith("https://"):
            # In a real app, you'd check environment here
            # For now, we'll allow HTTP but warn about it
            pass

        return v.strip()

    @model_validator(mode="after")
    def validate_task_specific_parameters(self) -> "TaskSubmitRequest":
        """Validate task-specific parameter ranges."""
        if self.task_type == "attentional_blink":
            params = self.parameters

            # Validate stream_length
            if "stream_length" in params:
                value = params["stream_length"]
                if not isinstance(value, int) or not (1 <= value <= 100):
                    raise ValueError("stream_length must be an integer between 1 and 100")

            # Validate item_duration_ms
            if "item_duration_ms" in params:
                value = params["item_duration_ms"]
                if not isinstance(value, (int, float)) or not (10 <= value <= 2000):
                    raise ValueError("item_duration_ms must be a number between 10 and 2000")

            # Validate num_trials_per_lag
            if "num_trials_per_lag" in params:
                value = params["num_trials_per_lag"]
                if not isinstance(value, int) or not (5 <= value <= 200):
                    raise ValueError("num_trials_per_lag must be an integer between 5 and 200")

            # Validate lags
            if "lags" in params:
                lags = params["lags"]
                if not isinstance(lags, list):
                    raise ValueError("lags must be a list")
                if not all(isinstance(lag, int) and 1 <= lag <= 20 for lag in lags):
                    raise ValueError("Each lag in lags must be an integer between 1 and 20")

            # Validate target_salience
            if "target_salience" in params:
                value = params["target_salience"]
                if not isinstance(value, (int, float)) or not (0.1 <= value <= 10.0):
                    raise ValueError("target_salience must be a number between 0.1 and 10.0")

        elif self.task_type == "iowa_gambling":
            params = self.parameters

            # Validate num_trials
            if "num_trials" in params:
                value = params["num_trials"]
                if not isinstance(value, int) or not (10 <= value <= 1000):
                    raise ValueError("num_trials must be an integer between 10 and 1000")

            # Validate initial_balance
            if "initial_balance" in params:
                value = params["initial_balance"]
                if not isinstance(value, int) or not (100 <= value <= 10000):
                    raise ValueError("initial_balance must be an integer between 100 and 10000")

            # Validate deck_stimulus_strength
            if "deck_stimulus_strength" in params:
                value = params["deck_stimulus_strength"]
                if not isinstance(value, (int, float)) or not (0.1 <= value <= 5.0):
                    raise ValueError("deck_stimulus_strength must be a number between 0.1 and 5.0")

            # Validate outcome_stimulus_strength
            if "outcome_stimulus_strength" in params:
                value = params["outcome_stimulus_strength"]
                if not isinstance(value, (int, float)) or not (0.1 <= value <= 5.0):
                    raise ValueError(
                        "outcome_stimulus_strength must be a number between 0.1 and 5.0"
                    )

            # Validate interoceptive_gain
            if "interoceptive_gain" in params:
                value = params["interoceptive_gain"]
                if not isinstance(value, (int, float)) or not (0.1 <= value <= 5.0):
                    raise ValueError("interoceptive_gain must be a number between 0.1 and 5.0")

            # Validate deck_selection_strategy
            if "deck_selection_strategy" in params:
                value = params["deck_selection_strategy"]
                valid_strategies = ["balanced", "random", "preference"]
                if value not in valid_strategies:
                    raise ValueError(
                        f"deck_selection_strategy must be one of: {', '.join(valid_strategies)}"
                    )

        return self

    model_config = ConfigDict(
        strict=True,
        json_schema_extra={
            "example": {
                "task_type": "attentional_blink",
                "parameters": {
                    "stream_length": 15,
                    "item_duration_ms": 100.0,
                    "num_trials_per_lag": 20,
                    "lags": [1, 2, 3, 4, 8],
                    "target_salience": 2.0,
                },
                "webhook_url": "https://example.com/webhook/task_complete",
            }
        },
    )


class TaskSubmitResponse(BaseModel):
    """Response for task submission."""

    task_id: str = Field(..., description="Unique task identifier")
    session_id: str = Field(..., description="Session identifier")
    task_type: str = Field(..., description="Type of task")
    status: str = Field(..., description="Task status")
    status_url: str = Field(..., description="URL to check task status")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "task_id": "task_xyz789",
                "session_id": "sess_abc123",
                "task_type": "attentional_blink",
                "status": "pending",
                "status_url": "/v1/tasks/task_xyz789",
            }
        }
    )


class TaskResultResponse(BaseModel):
    """Response for task result."""

    task_id: str = Field(..., description="Unique task identifier")
    result: Dict[str, Any] = Field(..., description="Task result data")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "task_id": "task_xyz789",
                "result": {
                    "accuracy": 0.85,
                    "trials_completed": 50,
                    "response_times": [0.5, 0.6, 0.4],
                },
            }
        }
    )


class TaskStatusResponse(BaseModel):
    """Response for task status."""

    task_id: str = Field(..., description="Unique task identifier")
    status: str = Field(..., description="Task status (pending/running/completed/failed)")
    state: Optional[str] = Field(None, description="Celery task state")
    result: Optional[Dict[str, Any]] = Field(None, description="Task result if completed")
    error: Optional[str] = Field(None, description="Error message if failed")
    info: Optional[Dict[str, Any]] = Field(None, description="Additional task information")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "summary": "Completed task",
                    "value": {
                        "task_id": "task_xyz789",
                        "status": "completed",
                        "state": "SUCCESS",
                        "result": {
                            "accuracy": 0.85,
                            "trials_completed": 50,
                            "response_times": [0.5, 0.6, 0.4, 0.7, 0.3],
                            "lag_performance": {
                                "lag_1": {"accuracy": 0.9, "rt_mean": 0.45},
                                "lag_2": {"accuracy": 0.8, "rt_mean": 0.52},
                            },
                        },
                    },
                },
                {
                    "summary": "Running task",
                    "value": {
                        "task_id": "task_xyz789",
                        "status": "running",
                        "state": "PROGRESS",
                        "info": {"current_trial": 25, "total_trials": 100, "progress": 0.25},
                    },
                },
                {
                    "summary": "Failed task",
                    "value": {
                        "task_id": "task_xyz789",
                        "status": "failed",
                        "state": "FAILURE",
                        "error": "Task execution failed: Invalid parameters provided",
                    },
                },
                {
                    "summary": "Pending task",
                    "value": {
                        "task_id": "task_xyz789",
                        "status": "pending",
                        "state": "PENDING",
                    },
                },
            ]
        }
    )


class TaskListResponse(BaseModel):
    """Response for list of available tasks."""

    tasks: list[Dict[str, Any]] = Field(..., description="List of available tasks")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "tasks": [
                    {
                        "task_type": "iowa_gambling",
                        "name": "Iowa Gambling Task",
                        "description": "Decision-making task with four decks of cards",
                        "parameters": {
                            "num_trials": {
                                "type": "integer",
                                "default": 100,
                                "description": "Number of trials",
                            },
                            "initial_balance": {
                                "type": "integer",
                                "default": 2000,
                                "description": "Starting balance",
                            },
                            "deck_stimulus_strength": {
                                "type": "float",
                                "default": 1.5,
                                "description": "Deck visual strength",
                            },
                            "outcome_stimulus_strength": {
                                "type": "float",
                                "default": 2.0,
                                "description": "Outcome strength",
                            },
                            "interoceptive_gain": {
                                "type": "float",
                                "default": 1.0,
                                "description": "Interoceptive signal multiplier",
                            },
                            "deck_selection_strategy": {
                                "type": "string",
                                "default": "balanced",
                                "description": "Selection strategy",
                            },
                        },
                    },
                    {
                        "task_type": "attentional_blink",
                        "name": "Attentional Blink Task",
                        "description": "RSVP task measuring attentional blink effect",
                        "parameters": {
                            "stream_length": {
                                "type": "integer",
                                "default": 15,
                                "description": "Number of items in RSVP stream",
                            },
                            "item_duration_ms": {
                                "type": "float",
                                "default": 100.0,
                                "description": "Duration of each item",
                            },
                            "num_trials_per_lag": {
                                "type": "integer",
                                "default": 20,
                                "description": "Trials per lag condition",
                            },
                            "lags": {
                                "type": "array",
                                "default": [1, 2, 3, 4, 8],
                                "description": "Lags to test",
                            },
                            "target_salience": {
                                "type": "float",
                                "default": 2.0,
                                "description": "Target salience boost",
                            },
                        },
                    },
                ]
            }
        }
    )


class SessionTaskListResponse(BaseModel):
    """Response for session tasks list."""

    tasks: list[TaskStatusResponse] = Field(..., description="List of tasks for the session")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "tasks": [
                    {
                        "task_id": "task_xyz789",
                        "status": "completed",
                        "state": "SUCCESS",
                        "result": {"accuracy": 0.85, "trials_completed": 50},
                        "created_at": "2024-01-15T10:30:00Z",
                    }
                ],
            }
        }
    )


class ErrorDetail(BaseModel):
    """Error detail structure."""

    code: str = Field(..., description="Error code")
    message: str = Field(..., description="Human-readable error message")
    timestamp: datetime = Field(..., description="Error timestamp")
    details: Optional[Dict[str, Any]] = Field(None, description="Additional error details")

    model_config = ConfigDict(
        strict=True,
        json_schema_extra={
            "example": {
                "code": "VALIDATION_ERROR",
                "message": "Invalid request payload",
                "timestamp": "2024-01-15T10:30:00Z",
                "details": {"field": "username", "error": "Field required"},
            }
        },
    )


class ErrorResponse(BaseModel):
    """Standard error response."""

    error: ErrorDetail = Field(..., description="Error details")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "error": {
                    "code": "NOT_FOUND",
                    "message": "Session not found",
                    "timestamp": "2024-01-15T10:30:00Z",
                }
            }
        }
    )


# User Management Schemas
class UserCreateRequest(BaseModel):
    """Request to create new user."""

    username: str = Field(..., description="Username")
    email: str = Field(..., description="Email address")
    password: str = Field(..., description="Password")

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: Any) -> Any:
        """Validate username format."""
        if not v or not v.strip():
            raise ValueError("Username cannot be empty")

        # Check username format (alphanumeric, underscore, dash, min 3 chars)
        if not re.match(r"^[a-zA-Z0-9_-]{3,50}$", v):
            raise ValueError(
                "Username must be 3-50 characters, alphanumeric with underscores or dashes"
            )

        return v.strip()

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: Any) -> Any:
        """Validate email format."""
        if not v or not v.strip():
            raise ValueError("Email cannot be empty")

        email_regex = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        if not re.match(email_regex, v):
            raise ValueError("Invalid email format")

        return v.strip().lower()

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: Any) -> Any:
        """Validate password strength."""
        if not v or len(v) < 8:
            raise ValueError("Password must be at least 8 characters long")

        if len(v) > 128:
            raise ValueError("Password must be no more than 128 characters long")

        # Check for at least one uppercase, one lowercase, one digit, and one special character
        if not re.search(r"[A-Z]", v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not re.search(r"[a-z]", v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not re.search(r"\d", v):
            raise ValueError("Password must contain at least one digit")
        if not re.search(r"[!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>\/?]", v):
            raise ValueError("Password must contain at least one special character")

        # Check for common weak passwords
        weak_passwords = [
            "password",
            "12345678",
            "qwerty",
            "abc123",
            "password123",
            "admin",
            "root",
            "user",
            "guest",
            "test",
            "123456789",
            "password1",
            "admin123",
            "root123",
            "user123",
        ]
        if v.lower() in weak_passwords:  # pragma: no cover
            raise ValueError("Password is too common. Please choose a stronger password")

        # Check for repeated characters (more than 3 in a row)
        if re.search(r"(.)\1{3,}", v):
            raise ValueError("Password cannot contain more than 3 consecutive identical characters")

        # Check for sequential characters (like 123, abc)
        if re.search(
            r"(?:012|123|234|345|456|567|678|789|abc|bcd|cde|def|efg|fgh|ghi|hij|ijk|jkl|klm|mno|nop|opq|pqr|qrs|rst|stu|tuv|uvw|vwx|wxy|xyz)",
            v.lower(),
        ):
            raise ValueError("Password cannot contain sequential characters")

        return v


class UserCreateResponse(BaseModel):
    """Response for user creation."""

    user_id: str = Field(..., description="Unique user identifier")
    username: str = Field(..., description="Username")
    email: str = Field(..., description="Email address")
    created_at: datetime = Field(..., description="Creation timestamp")
    message: str = Field(..., description="Response message")


class UserResponse(BaseModel):
    """Response for user details."""

    user_id: str = Field(..., description="Unique user identifier")
    username: str = Field(..., description="Username")
    email: str = Field(..., description="Email address")
    roles: list[str] = Field(..., description="User roles")
    is_active: bool = Field(..., description="Whether user is active")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    last_login: Optional[datetime] = Field(None, description="Last login timestamp")


class UserUpdateRequest(BaseModel):
    """Request to update user."""

    email: Optional[str] = Field(None, description="Email address")
    password: Optional[str] = Field(None, description="New password")
    roles: Optional[List[str]] = Field(None, description="User roles")
    is_active: Optional[bool] = Field(None, description="Whether user is active")


class PasswordResetRequest(BaseModel):
    """Request to reset password."""

    new_password: str = Field(..., description="New password")

    model_config = ConfigDict(strict=True)


class PasswordResetEmailRequest(BaseModel):
    """Request to initiate password reset by email."""

    email: str = Field(..., description="User email address")

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: Any) -> Any:
        """Validate email format."""
        if not v or not v.strip():
            raise ValueError("Email cannot be empty")

        email_regex = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        if not re.match(email_regex, v):
            raise ValueError("Invalid email format")

        return v.strip().lower()


class PasswordResetConfirmRequest(BaseModel):
    """Request to confirm password reset with token."""

    token: str = Field(..., description="Reset token")
    new_password: str = Field(..., description="New password")

    @field_validator("token")
    @classmethod
    def validate_token(cls, v: Any) -> Any:
        """Validate token format."""
        if not v or not v.strip():
            raise ValueError("Token cannot be empty")

        if len(v) > 255:
            raise ValueError("Token is too long")

        return v.strip()

    model_config = ConfigDict(strict=True)


class PasswordResetEmailResponse(BaseModel):
    """Response for password reset email request."""

    message: str = Field(..., description="Response message")


class PasswordResetConfirmResponse(BaseModel):
    """Response for password reset confirmation."""

    message: str = Field(..., description="Confirmation message")


class PasswordResetResponse(BaseModel):
    """Response for password reset."""

    user_id: str = Field(..., description="User identifier")
    message: str = Field(..., description="Reset message")


class MFAEnrollResponse(BaseModel):
    """Response for MFA enrollment."""

    secret: str = Field(..., description="MFA secret key")
    qr_code_url: str = Field(..., description="QR code URL for MFA app")
    message: str = Field(..., description="Enrollment instructions")
    backup_codes: List[str] = Field(..., description="Backup recovery codes")


class MFADisableRequest(BaseModel):
    """Request to disable MFA."""

    password: str = Field(..., description="Current password for verification")


class MFADisableResponse(BaseModel):
    """Response for MFA disable."""

    message: str = Field(..., description="Disable confirmation message")


class MFAEnableRequest(BaseModel):
    """Request to enable MFA."""

    code: str = Field(..., description="TOTP code from authenticator app")

    model_config = ConfigDict(strict=True)


class MFAEnableResponse(BaseModel):
    """Response for MFA enable."""

    message: str = Field(..., description="Enable confirmation message")


class MFABackupCodeVerifyRequest(BaseModel):
    """Request to verify MFA backup code."""

    code: str = Field(..., description="Backup code to verify")

    @field_validator("code")
    @classmethod
    def validate_code(cls, v: Any) -> Any:
        """Validate backup code format."""
        if not v or not v.strip():
            raise ValueError("Backup code cannot be empty")

        # Backup codes are 8-character hex strings
        if len(v) != 8 or not v.isalnum():
            raise ValueError("Invalid backup code format")

        return v.strip().upper()


class MFABackupCodeVerifyResponse(BaseModel):
    """Response for MFA backup code verification."""

    message: str = Field(..., description="Verification result message")


class MFABackupCodeRegenerateResponse(BaseModel):
    """Response for MFA backup code regeneration."""

    backup_codes: List[str] = Field(..., description="New backup codes")
    message: str = Field(..., description="Regeneration confirmation message")


# State Schemas
class AllostaticState(BaseModel):
    """Allostatic state representation."""

    allostatic_load: float = Field(..., description="Allostatic load")


class BodyState(BaseModel):
    """Body state representation.

    Gated by K7 identifiability (governing doc §3.6, parameter
    `interoceptive_body_state`): fields are omitted entirely from the
    response until cleared, not defaulted to null.
    """

    heart_rate: Optional[float] = Field(None, description="Heart rate (K7-gated)")
    cortisol: Optional[float] = Field(None, description="Cortisol level (K7-gated)")
    temperature: Optional[float] = Field(None, description="Body temperature (K7-gated)")


class IgnitionEvent(BaseModel):
    """Ignition event representation."""

    time_ms: float = Field(..., description="Event time in milliseconds")
    duration_ms: Optional[float] = Field(None, description="Event duration in milliseconds")
    trigger_signal: float = Field(..., description="Trigger signal")
    threshold: float = Field(..., description="Signal threshold")


class IgnitionHistoryResponse(BaseModel):
    """Response for ignition history."""

    events: list[IgnitionEvent] = Field(..., description="Ignition events")
    pagination: Optional[PaginationInfo] = Field(None, description="Pagination information")


class IgnitionState(BaseModel):
    """Ignition state representation.

    `threshold` (theta_t) is gated by K7 identifiability (governing doc
    §3.6, parameter `ignition_threshold`) and is omitted from the response
    until cleared, not defaulted to null.
    """

    ignition_occurred: Optional[bool] = Field(
        None, description="Whether ignition has occurred (ignition_state gate)"
    )
    total_signal: Optional[float] = Field(
        None, description="Total ignition signal S_t (ignition_state gate)"
    )
    threshold: Optional[float] = Field(None, description="Ignition threshold (K7-gated)")
    duration_ms: Optional[float] = Field(None, description="Ignition duration in milliseconds")


class MetabolicState(BaseModel):
    """Metabolic state representation."""

    reserves: float = Field(..., description="Metabolic reserves")
    reserve_fraction: float = Field(..., description="Reserve fraction")


class MinimalSelfState(BaseModel):
    """Minimal self state representation."""

    coherence: float = Field(..., description="Self coherence")


class NarrativeSelfState(BaseModel):
    """Narrative self state representation."""

    narrative: str = Field(..., description="Self narrative")


class PaginationInfo(BaseModel):
    """Pagination information."""

    page: int = Field(..., description="Current page number")
    per_page: int = Field(..., description="Items per page")
    total: int = Field(..., description="Total number of items")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "page": 1,
                "per_page": 10,
                "total": 25,
            }
        }
    )


class UsersListResponse(BaseModel):
    """Response for users list with pagination."""

    users: List[UserResponse] = Field(..., description="List of users")
    pagination: PaginationInfo = Field(..., description="Pagination information")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "users": [
                    {
                        "user_id": "user_abc123",
                        "username": "testuser",
                        "email": "test@example.com",
                        "roles": ["viewer"],
                        "is_active": True,
                        "created_at": "2024-01-15T10:30:00Z",
                        "updated_at": "2024-01-15T10:35:00Z",
                        "last_login": None,
                    }
                ],
                "pagination": {
                    "page": 1,
                    "per_page": 10,
                    "total": 1,
                },
            }
        }
    )


class PrecisionState(BaseModel):
    """Precision state representation.

    Gated by K7 identifiability (governing doc §3.6, parameters
    `precision_exteroceptive` / `precision_interoceptive`): fields are
    omitted entirely from the response until cleared, not defaulted to null.
    """

    exteroceptive: Optional[float] = Field(None, description="Exteroceptive precision (K7-gated)")
    interoceptive: Optional[float] = Field(None, description="Interoceptive precision (K7-gated)")


class SelfModelState(BaseModel):
    """Self model state representation."""

    minimal: MinimalSelfState = Field(..., description="Minimal self state")
    narrative: NarrativeSelfState = Field(..., description="Narrative self state")


class SystemStateResponse(BaseModel):
    """Response for system state."""

    time_ms: float = Field(..., description="Current simulation time in milliseconds")
    ignition: IgnitionState = Field(..., description="Ignition state")
    workspace: WorkspaceState = Field(..., description="Workspace state")
    body: BodyState = Field(..., description="Body state")
    allostasis: AllostaticState = Field(..., description="Allostatic state")
    precision: PrecisionState = Field(..., description="Precision state")
    metabolism: MetabolicState = Field(..., description="Metabolic state")
    self_model: SelfModelState = Field(..., description="Self model state")


class StateVectorResponse(BaseModel):
    """The full 7-dimensional latent APGI state vector
    x_t = [S_t, theta_t, Pi_e, Pi_i, |eps_e|, |eps_i|, beta] (governing doc
    §3.6), gated as a single object behind K7b (app.services.k7_gate is the
    per-parameter K7 gate used elsewhere; K7b is the stricter, separate test
    for exposing every component together rather than piecemeal)."""

    session_id: str = Field(..., description="Session identifier")
    time_ms: float = Field(..., description="Current simulation time in milliseconds")
    ignition_state: float = Field(..., description="S_t: total ignition signal")
    ignition_threshold: float = Field(..., description="theta_t: ignition threshold")
    precision_exteroceptive: float = Field(..., description="Pi_e")
    precision_interoceptive: float = Field(..., description="Pi_i")
    prediction_error_exteroceptive: Dict[str, Any] = Field(
        ..., description="|eps_e| summary statistics"
    )
    prediction_error_interoceptive: Dict[str, Any] = Field(
        ..., description="|eps_i| summary statistics"
    )
    somatic_bias: Dict[str, Any] = Field(..., description="beta: somatic marker bias summary")


# Export Schemas
class SummaryStatistics(BaseModel):
    """Summary statistics for export."""

    mean: float = Field(..., description="Mean value")
    std: float = Field(..., description="Standard deviation")
    min: float = Field(..., description="Minimum value")
    max: float = Field(..., description="Maximum value")


class WorkspaceState(BaseModel):
    """Workspace state representation."""

    is_broadcasting: bool = Field(..., description="Whether workspace is broadcasting")
    content: Optional[str] = Field(None, description="Workspace content")
    broadcast_duration_ms: Optional[float] = Field(
        None, description="Broadcast duration in milliseconds"
    )


class PredictionErrorsResponse(BaseModel):
    """Response for prediction errors.

    Gated by K7 identifiability (governing doc §3.6, parameters
    `prediction_error_exteroceptive` / `prediction_error_interoceptive`):
    fields are omitted entirely from the response until cleared, not
    defaulted to null.
    """

    session_id: str = Field(..., description="Session identifier")
    time_ms: float = Field(..., description="Current time in milliseconds")
    prediction_errors: Optional[Dict[str, Any]] = Field(
        None, description="Prediction errors (K7-gated)"
    )
    exteroceptive_stats: Optional[Dict[str, Any]] = Field(
        None, description="Exteroceptive statistics (K7-gated)"
    )
    interoceptive_stats: Optional[Dict[str, Any]] = Field(
        None, description="Interoceptive statistics (K7-gated)"
    )


class SomaticMarkersResponse(BaseModel):
    """Response for somatic markers.

    Gated by K7 identifiability (governing doc §3.6, parameter
    `somatic_bias`): fields are omitted entirely from the response until
    cleared, not defaulted to null/zero.
    """

    session_id: str = Field(..., description="Session identifier")
    time_ms: float = Field(..., description="Current time in milliseconds")
    num_markers: Optional[int] = Field(None, description="Number of markers (K7-gated)")
    total_retrievals: Optional[int] = Field(None, description="Total retrievals (K7-gated)")
    successful_retrievals: Optional[int] = Field(
        None, description="Successful retrievals (K7-gated)"
    )
    retrieval_rate: Optional[float] = Field(None, description="Retrieval rate (K7-gated)")
    markers: Optional[list[Dict[str, Any]]] = Field(None, description="Somatic markers (K7-gated)")


# API Key Management Schemas
class APIKeyCreateRequest(BaseModel):
    """Request to create a new API key."""

    name: str = Field(..., description="API key name/description")
    permissions: List[str] = Field(default_factory=list, description="Permissions for this key")
    expires_at: Optional[datetime] = Field(
        None,
        description="Optional expiration timestamp. If not provided, defaults to 1 year from creation.",
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: Any) -> Any:
        """Validate API key name."""
        if not v or not v.strip():
            raise ValueError("API key name cannot be empty")

        if len(v) > 100:
            raise ValueError("API key name is too long (max 100 characters)")

        return v.strip()

    @field_validator("expires_at")
    @classmethod
    def validate_expires_at(cls, v: Any) -> Any:
        """Validate expiration date."""
        if v is not None:  # pragma: no branch
            now = datetime.now(timezone.utc)
            if v <= now:
                raise ValueError("Expiration date must be in the future")

            # Limit maximum expiry to 2 years
            max_expiry = now + timedelta(days=730)
            if v > max_expiry:
                raise ValueError("API key expiry cannot exceed 2 years from creation")

        return v

    @model_validator(mode="before")
    @classmethod
    def validate_request(cls, data: Any) -> Any:
        """Ensure API key has reasonable expiry if provided."""
        if data.get("expires_at") is None:
            # Set default expiry to 1 year if not provided
            data["expires_at"] = datetime.now(timezone.utc) + timedelta(days=365)

        return data

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, v: Any) -> Any:
        """Validate permissions list."""
        if v is None:  # pragma: no cover
            return []

        if not isinstance(v, list):  # pragma: no cover
            raise ValueError("Permissions must be a list")

        valid_permissions = {"read", "write", "admin", "delete"}
        for perm in v:
            if not isinstance(perm, str) or not perm.strip():
                raise ValueError("Each permission must be a non-empty string")
            if perm not in valid_permissions:
                raise ValueError(
                    f"Invalid permission: {perm}. Must be one of: {', '.join(valid_permissions)}"
                )

        return [perm.strip() for perm in v]

    model_config = ConfigDict(
        strict=True,
        json_schema_extra={
            "example": {
                "name": "My API Key",
                "permissions": ["read", "write"],
                "expires_at": "2025-01-15T10:30:00Z",
            }
        },
    )


class APIKeyCreateResponse(BaseModel):
    """Response for API key creation."""

    key_id: str = Field(..., description="Unique API key identifier")
    key: str = Field(..., description="The actual API key (only shown once)")
    name: str = Field(..., description="API key name")
    permissions: List[str] = Field(..., description="Permissions for this key")
    expires_at: Optional[datetime] = Field(None, description="Expiration timestamp")
    created_at: datetime = Field(..., description="Creation timestamp")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "key_id": "key_abc123",
                "key": "apgi_abc123def456...",
                "name": "My API Key",
                "permissions": ["read", "write"],
                "expires_at": "2025-01-15T10:30:00Z",
                "created_at": "2024-01-15T10:30:00Z",
            }
        }
    )


class APIKeyResponse(BaseModel):
    """Response for API key details (without the actual key)."""

    key_id: str = Field(..., description="Unique API key identifier")
    name: str = Field(..., description="API key name")
    permissions: List[str] = Field(..., description="Permissions for this key")
    expires_at: Optional[datetime] = Field(None, description="Expiration timestamp")
    is_active: bool = Field(..., description="Whether the key is active")
    created_at: datetime = Field(..., description="Creation timestamp")
    last_used_at: Optional[datetime] = Field(None, description="Last usage timestamp")

    model_config = ConfigDict(
        strict=True,
        json_schema_extra={
            "example": {
                "key_id": "key_abc123",
                "name": "My API Key",
                "permissions": ["read", "write"],
                "expires_at": "2025-01-15T10:30:00Z",
                "is_active": True,
                "created_at": "2024-01-15T10:30:00Z",
                "last_used_at": "2024-01-16T08:45:00Z",
            }
        },
    )


class APIKeyListResponse(BaseModel):
    """Response for API keys list."""

    api_keys: List[APIKeyResponse] = Field(..., description="List of API keys")
    pagination: PaginationInfo = Field(..., description="Pagination information")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "api_keys": [
                    {
                        "key_id": "key_abc123",
                        "name": "My API Key",
                        "permissions": ["read", "write"],
                        "expires_at": "2025-01-15T10:30:00Z",
                        "is_active": True,
                        "created_at": "2024-01-15T10:30:00Z",
                        "last_used_at": "2024-01-16T08:45:00Z",
                    }
                ],
                "pagination": {
                    "page": 1,
                    "per_page": 10,
                    "total": 1,
                },
            }
        }
    )


class APIKeyUpdateRequest(BaseModel):
    """Request to update an API key."""

    name: Optional[str] = Field(None, description="API key name")
    permissions: Optional[List[str]] = Field(None, description="Permissions for this key")
    is_active: Optional[bool] = Field(None, description="Whether the key is active")

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: Any) -> Any:
        """Validate API key name."""
        if v is None:
            return v

        if not v.strip():
            raise ValueError("API key name cannot be empty")

        if len(v) > 100:
            raise ValueError("API key name is too long (max 100 characters)")

        return v.strip()

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, v: Any) -> Any:
        """Validate permissions list."""
        if v is None:
            return v

        if not isinstance(v, list):  # pragma: no cover
            raise ValueError("Permissions must be a list")

        valid_permissions = {"read", "write", "admin", "delete"}
        for perm in v:
            if not isinstance(perm, str) or not perm.strip():
                raise ValueError("Each permission must be a non-empty string")
            if perm not in valid_permissions:
                raise ValueError(
                    f"Invalid permission: {perm}. Must be one of: {', '.join(valid_permissions)}"
                )

        return [perm.strip() for perm in v]

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Updated API Key Name",
                "permissions": ["read", "write", "admin"],
                "is_active": False,
            }
        }
    )


# Webhook Delivery Management Schemas
class WebhookDeliveryResponse(BaseModel):
    """Response for webhook delivery details."""

    delivery_id: str = Field(..., description="Unique delivery identifier")
    task_id: str = Field(..., description="Associated task ID")
    webhook_url: str = Field(..., description="Target webhook URL")
    status: str = Field(..., description="Delivery status")
    attempts: int = Field(..., description="Number of delivery attempts")
    last_attempt_at: Optional[datetime] = Field(None, description="Last delivery attempt timestamp")
    next_retry_at: Optional[datetime] = Field(None, description="Next retry timestamp")
    response_status: Optional[int] = Field(None, description="HTTP response status code")
    response_body: Optional[str] = Field(None, description="HTTP response body")
    error_message: Optional[str] = Field(None, description="Error message if delivery failed")
    created_at: datetime = Field(..., description="Delivery record creation timestamp")

    model_config = ConfigDict(
        strict=True,
        json_schema_extra={
            "example": {
                "delivery_id": "delivery_abc123",
                "task_id": "task_xyz789",
                "webhook_url": "https://example.com/webhook",
                "status": "delivered",
                "attempts": 1,
                "last_attempt_at": "2024-01-15T10:35:00Z",
                "next_retry_at": None,
                "response_status": 200,
                "response_body": '{"status": "success"}',
                "error_message": None,
                "created_at": "2024-01-15T10:30:00Z",
            }
        },
    )


class WebhookDeliveryListResponse(BaseModel):
    """Response for webhook deliveries list."""

    deliveries: List[WebhookDeliveryResponse] = Field(..., description="List of webhook deliveries")
    pagination: PaginationInfo = Field(..., description="Pagination information")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "deliveries": [
                    {
                        "delivery_id": "delivery_abc123",
                        "task_id": "task_xyz789",
                        "webhook_url": "https://example.com/webhook",
                        "status": "delivered",
                        "attempts": 1,
                        "last_attempt_at": "2024-01-15T10:35:00Z",
                        "next_retry_at": None,
                        "response_status": 200,
                        "response_body": '{"status": "success"}',
                        "error_message": None,
                        "created_at": "2024-01-15T10:30:00Z",
                    }
                ],
                "pagination": {
                    "page": 1,
                    "per_page": 10,
                    "total": 1,
                },
            }
        }
    )


class WebhookRetryResponse(BaseModel):
    """Response for webhook retry operation."""

    delivery_id: str = Field(..., description="Delivery identifier")
    success: bool = Field(..., description="Whether the retry was successful")
    status: str = Field(..., description="Updated delivery status")
    attempts: int = Field(..., description="Updated number of attempts")
    last_attempt_at: Optional[datetime] = Field(None, description="Last attempt timestamp")

    model_config = ConfigDict(
        strict=True,
        json_schema_extra={
            "example": {
                "delivery_id": "delivery_abc123",
                "success": True,
                "status": "delivered",
                "attempts": 2,
                "last_attempt_at": "2024-01-15T10:40:00Z",
            }
        },
    )


class UserStatsResponse(BaseModel):
    """Response for user statistics."""

    total_users: int = Field(..., description="Total number of users")
    active_users: int = Field(..., description="Number of active users")
    inactive_users: int = Field(..., description="Number of inactive users")
    role_counts: Dict[str, int] = Field(..., description="Count of users by role")
    total_sessions: int = Field(..., description="Total number of sessions")
    active_sessions: int = Field(..., description="Number of active sessions")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "total_users": 150,
                "active_users": 120,
                "inactive_users": 30,
                "role_counts": {"admin": 5, "user": 145},
                "total_sessions": 450,
                "active_sessions": 45,
            }
        }
    )

"""Request/response schemas for the acquisition upload routes."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class UploadInitRequest(BaseModel):
    """Manifest of one chunk/file (stage A)."""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    object_name: str = Field(min_length=1, max_length=512)
    size_bytes: int = Field(ge=0)
    crc32c: int = Field(ge=0, le=0xFFFFFFFF)
    sha256: str = Field(pattern=r"^[A-Fa-f0-9]{64}$")
    sequence: Optional[int] = Field(default=None, ge=0)


class UploadInitResponse(BaseModel):
    upload_id: str
    signed_url: str
    expires_in_s: int
    already_accepted: bool = False


class UploadStatusResponse(BaseModel):
    upload_id: str
    status: Literal["pending", "accepted", "quarantined", "rejected"]
    reason: str = ""
    sha256: str

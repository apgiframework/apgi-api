"""
Pydantic schemas for the research pilot domain (participants, consent,
studies, batteries, participant sessions, trial events).

Every request schema sets `extra="forbid"`: a client-supplied field that
isn't explicitly declared (e.g. trying to set `is_deleted`, `session_index`,
or `status` directly on a create request) is rejected with a 422 rather
than silently ignored or silently accepted — the hidden-field tampering
control called for in the governing doc's Phase 2 checklist.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.database.models import ConsentType, ParticipantSessionStatus, StudyStatus

_FORBID_EXTRA = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Participants
# ---------------------------------------------------------------------------


class ParticipantCreateRequest(BaseModel):
    model_config = _FORBID_EXTRA

    external_ref: Optional[str] = Field(
        None, max_length=255, description="Pseudonymous panel/recruitment ID"
    )
    contact_email: Optional[str] = Field(
        None, max_length=320, description="Contact email (will be encrypted at rest)"
    )
    demographics: Optional[dict[str, Any]] = Field(
        None, description="Self-reported demographics (will be encrypted at rest)"
    )


class ParticipantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    participant_id: str
    external_ref: Optional[str] = None
    is_deleted: bool
    created_at: datetime


# ---------------------------------------------------------------------------
# Consent
# ---------------------------------------------------------------------------


class ConsentGrantRequest(BaseModel):
    model_config = _FORBID_EXTRA

    consent_type: ConsentType
    locale: str = Field("en", min_length=2, max_length=10)


class ConsentRevokeRequest(BaseModel):
    model_config = _FORBID_EXTRA

    consent_type: ConsentType


class ConsentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    consent_id: str
    consent_type: ConsentType
    version: str
    granted_at: datetime
    revoked_at: Optional[datetime] = None


class ConsentStatusResponse(BaseModel):
    """Effective (unrevoked, current-version) status for each consent purpose."""

    product_use: bool = False
    research_participation: bool
    data_sharing: bool
    recontact: bool = False


# ---------------------------------------------------------------------------
# Studies
# ---------------------------------------------------------------------------


class StudyCreateRequest(BaseModel):
    model_config = _FORBID_EXTRA

    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    osf_registration_url: Optional[str] = Field(None, max_length=500)


class StudyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    study_id: str
    name: str
    description: Optional[str] = None
    osf_registration_url: Optional[str] = None
    status: StudyStatus
    created_at: datetime


# ---------------------------------------------------------------------------
# Batteries
# ---------------------------------------------------------------------------


class BatteryCreateRequest(BaseModel):
    model_config = _FORBID_EXTRA

    study_id: str
    name: str = Field(..., min_length=1, max_length=200)
    version: str = Field(..., min_length=1, max_length=20)
    form_label: str = Field("A", max_length=10)
    instrument_schema: Optional[dict[str, Any]] = None


class BatteryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    battery_id: str
    study_id: str
    name: str
    version: str
    form_label: str
    created_at: datetime


# ---------------------------------------------------------------------------
# Participant Sessions
# ---------------------------------------------------------------------------


class ParticipantSessionCreateRequest(BaseModel):
    model_config = _FORBID_EXTRA

    participant_id: str
    study_id: str
    battery_id: str
    device_fingerprint: Optional[str] = Field(
        None,
        max_length=512,
        description="Client device fingerprint; stored only as a peppered hash",
    )
    data_origin: Optional[Literal["synthetic", "human_observed"]] = Field(
        None, description="Factual origin of this session's data; omit if unknown"
    )
    administration_mode: Optional[Literal["web", "native_macos"]] = Field(
        None, description="How the battery was administered; omit if unknown"
    )
    app_version: Optional[str] = Field(
        None, max_length=40, pattern=r"^[A-Za-z0-9._+-]+$", description="Client build version"
    )
    browser_family: Optional[str] = Field(
        None, max_length=40, pattern=r"^[A-Za-z0-9 ._-]+$", description="Browser family, if web"
    )


class ParticipantSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    participant_session_id: str
    participant_id: str
    study_id: str
    battery_id: str
    session_index: int
    status: ParticipantSessionStatus
    scores: Optional[dict[str, Any]] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    device_tier: Optional[str] = None
    tier_config_version: Optional[str] = None
    norm_eligible: Optional[bool] = None
    expected_form_label: Optional[str] = None
    release_state: str


# ---------------------------------------------------------------------------
# Trial events
# ---------------------------------------------------------------------------


class TrialEventIngestRequest(BaseModel):
    model_config = _FORBID_EXTRA

    event_id: str = Field(
        ..., min_length=1, max_length=64, description="Stable client id; retries reuse it"
    )
    seq_no: int = Field(..., ge=0, description="Per-session sequence number")
    task_type: str = Field(..., min_length=1, max_length=50)
    trial_index: int = Field(..., ge=0)
    timing_provenance: Literal["client_monotonic", "client_wall", "device_sensor"] = Field(
        "client_monotonic",
        description="Source of rt_ms. HTTP request time is never used as event time.",
    )
    response_value: Optional[dict[str, Any]] = None
    rt_ms: Optional[float] = Field(None, ge=0)
    correct: Optional[bool] = None
    raw_payload: Optional[dict[str, Any]] = Field(
        None,
        description="Full raw trial payload (RT distributions, logs) — written to restricted storage, not Postgres",
    )


class TrialEventBatchIngestRequest(BaseModel):
    model_config = _FORBID_EXTRA

    events: list[TrialEventIngestRequest] = Field(..., min_length=1, max_length=500)


class TrialEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    trial_event_id: str
    event_id: Optional[str] = None
    seq_no: Optional[int] = None
    task_type: str
    trial_index: int
    received_at: datetime
    duplicate: bool = Field(False, description="True when this was an acknowledged retry")


class SessionCompletionResponse(BaseModel):
    """The session-completion contract: what a client gets back once a
    participant session is marked complete and handed to scoring."""

    participant_session_id: str
    status: ParticipantSessionStatus
    trial_count: int
    completed_at: Optional[datetime] = None
    awaiting_inputs: bool = False
    missing_seq_nos: List[int] = Field(default_factory=list)


class SessionQualityRequest(BaseModel):
    """Client-side device/timing telemetry and validity signals for one
    session, submitted before completion (tracker S3-BAT-01.4/.5/.6, S3-SCR-01)."""

    model_config = _FORBID_EXTRA

    tier_config_version: str = Field(..., max_length=40)
    timing_errors_ms: list[float] = Field(
        default_factory=list, max_length=20000, description="Per-trial timing errors"
    )
    dropped_frame_fraction: float = Field(0.0, ge=0, le=1)
    fullscreen: bool = False
    is_mobile: bool = False
    visibility_changes: int = Field(0, ge=0)
    attention_checks_passed: int = Field(0, ge=0)
    attention_checks_failed: int = Field(0, ge=0)
    questionnaire_responses: Optional[list[int]] = Field(None, max_length=500)
    honeypot: Optional[str] = Field(None, max_length=200)
    webdriver: bool = False
    completion_seconds: Optional[float] = Field(None, ge=0)


class ExclusionReportResponse(BaseModel):
    study_id: str
    battery_id: Optional[str] = None
    n_screened_sessions: int
    n_excluded_from_norms: int
    exclusion_rate: Optional[float] = None
    by_reason: dict[str, int]
    by_device_tier: dict[str, int]
    tier_config_version: str

"""Schemas for the Explorer research-client contract (config, item bank,
consent texts, idempotent journal events, allow-listed scores)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ConsentPurposeInfo(BaseModel):
    consent_type: str
    version: str
    status: str
    required_for_research: bool


class InstrumentRef(BaseModel):
    instrument_id: str
    bank_version: str
    sha256: str
    item_count: int


class ExplorerConfigResponse(BaseModel):
    research_enabled: bool
    paid_products_enabled: bool = False
    validation_results: bool = False
    instrument: InstrumentRef
    consent_purposes: list[ConsentPurposeInfo]


class ConsentTextResponse(BaseModel):
    consent_type: str
    version: str
    locale: str
    status: str
    required_for_research: bool
    title: str
    body: str
    sha256: str


class ItemOption(BaseModel):
    value: int
    text: str


class BankItem(BaseModel):
    item_id: int
    subscale: str
    scoring: Literal["ordinal", "nominal"]
    text: str
    options: list[ItemOption]


class ItemBankResponse(BaseModel):
    instrument_id: str
    bank_version: str
    locale: str
    sha256: str
    items: list[BankItem]


class ScoreAllowlistEntry(BaseModel):
    field: str
    label: str


class ScoreAllowlistResponse(BaseModel):
    instrument_id: str
    fields: list[ScoreAllowlistEntry]


class JournalEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(..., min_length=36, max_length=36, description="Client UUID")
    seq_no: int = Field(..., ge=0)
    payload: dict[str, Any]
    payload_sha256: str = Field(..., min_length=64, max_length=64)


class JournalEventBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[JournalEvent] = Field(..., min_length=1, max_length=200)


class EventAck(BaseModel):
    event_id: str
    seq_no: int
    payload_sha256: str
    status: Literal["accepted", "duplicate"]
    server_verified: bool


class EventAckBatch(BaseModel):
    acks: list[EventAck]


class ExplorerResultResponse(BaseModel):
    participant_session_id: str
    instrument_id: str
    instrument_sha256: str
    analysis_mode: str
    excluded_from_norms: bool
    scores: dict[str, float]

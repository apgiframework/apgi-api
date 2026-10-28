"""Pydantic schemas for norms, psychometrics, /v1/meta, and the dataset card."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class NormsResponse(BaseModel):
    study_id: str
    battery_id: str
    task_type: str
    metric: str
    score: float
    n: int
    percentile: float
    percentile_ci_lower: float
    percentile_ci_upper: float
    release_state: str
    note: Optional[str] = None


class PsychometricsResponse(BaseModel):
    battery_id: str
    task_type: str
    n_sessions: int
    n_items: int
    cronbachs_alpha: float
    mean_total_correct: Optional[float] = None
    sd_total_correct: Optional[float] = None
    release_state: str
    note: Optional[str] = None


class MetaResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    researcher: dict[str, Any]
    osf: dict[str, Any]
    zenodo: dict[str, Any]
    code: dict[str, Any]
    web: dict[str, Any]
    licences: dict[str, Any]
    release_state: dict[str, Any]
    ethics_number: Optional[str] = None  # null until IRB approval
    version_lattice: dict[str, Any] = {}


class DatasetCardResponse(BaseModel):
    release_state: dict[str, Any]
    licence: str
    ethics: dict[str, Any]
    counts: dict[str, int]
    warnings: list[str]


class LongitudinalMetricsResponse(BaseModel):
    study_id: str
    battery_id: str
    task_type: str
    metric: str
    session_index_a: int
    session_index_b: int
    n_subjects: int
    icc: float
    sem: float
    mdc95: float
    release_state: str
    note: Optional[str] = None


class ChangeReportResponse(BaseModel):
    participant_id: str
    task_type: str
    metric: str
    session_index_a: int
    session_index_b: int
    score_a: float
    score_b: float
    delta: float
    mdc95: Optional[float] = None
    reference_n_subjects: int
    classification: str
    release_state: str

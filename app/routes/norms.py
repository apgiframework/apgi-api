"""
Norms Endpoint (Phase 3-4).

Empirical norms computed from the pool of completed sessions for a given
battery+task, not an externally-validated normative table — this pilot has
no such table yet (identifiers.yaml: release_state.reference_dataset_version
is null). The response says so via `release_state` and `note` rather than
presenting pilot-derived percentiles as if they were clinically validated.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.database.models import Battery, ParticipantSession, ParticipantSessionStatus
from app.models.schemas import ErrorResponse, TokenPayload
from app.schemas.reporting import NormsResponse
from app.services.authorization import get_current_user
from app.services.identifiers import current_release_state
from app.services.stats import bootstrap_percentile_ci, percentile_rank

router = APIRouter(
    prefix="/v1/norms",
    tags=["Norms"],
    responses={404: {"model": ErrorResponse, "description": "Not Found"}},
)

_ALLOWED_METRICS = {"accuracy", "mean_rt_ms", "median_rt_ms"}


@router.get("", response_model=NormsResponse)
async def get_norms(
    study_id: str = Query(...),
    battery_id: str = Query(...),
    task_type: str = Query(...),
    metric: str = Query(..., description="accuracy | mean_rt_ms | median_rt_ms"),
    score: float = Query(..., description="The raw score to rank against the reference sample"),
    db: Session = Depends(get_db),
    _current_user: TokenPayload = Depends(get_current_user),
) -> NormsResponse:
    if metric not in _ALLOWED_METRICS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"metric must be one of {sorted(_ALLOWED_METRICS)}",
        )

    battery = db.query(Battery).filter(Battery.battery_id == battery_id).first()
    if battery is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Battery not found")

    # Only screened, norm-eligible sessions (tier A/B, no bot/attention failures,
    # first session per participant per battery version) contribute.
    sessions = (
        db.query(ParticipantSession)
        .filter(
            ParticipantSession.study_id == study_id,
            ParticipantSession.battery_id == battery_id,
            ParticipantSession.status == ParticipantSessionStatus.COMPLETED,
            ParticipantSession.scores.isnot(None),
            ParticipantSession.norm_eligible.is_(True),
        )
        .all()
    )

    sample = []
    for session in sessions:
        session_scores: dict[str, Any] = session.scores or {}  # type: ignore[assignment]
        tasks = session_scores.get("tasks", {})
        task_scores = tasks.get(task_type, {})
        if metric in task_scores:
            sample.append(float(task_scores[metric]))

    if not sample:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No scored sessions found for battery {battery_id}, task {task_type}",
        )

    rank = percentile_rank(sample, score)
    ci_lower, ci_upper = bootstrap_percentile_ci(sample, score)

    release_state = current_release_state()
    note = None
    if release_state != "calibrated":
        note = (
            f"release_state is '{release_state}': this norm is derived from pilot data pooled "
            "to date, not a validated normative sample. Do not present as clinically normed."
        )

    return NormsResponse(
        study_id=study_id,
        battery_id=battery_id,
        task_type=task_type,
        metric=metric,
        score=score,
        n=len(sample),
        percentile=round(rank, 2),
        percentile_ci_lower=round(ci_lower, 2),
        percentile_ci_upper=round(ci_upper, 2),
        release_state=release_state,
        note=note,
    )

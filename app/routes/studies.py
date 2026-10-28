"""Study Routes (Phase 2). Creating/managing studies is staff-only (researcher/admin)."""

from __future__ import annotations

from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.database.models import Study
from app.models.schemas import ErrorResponse, TokenPayload
from app.schemas.research import ExclusionReportResponse, StudyCreateRequest, StudyResponse
from app.services.authorization import Role, log_audit_event, require_any_role
from app.services.screening import exclusion_report

router = APIRouter(
    prefix="/v1/studies",
    tags=["Studies"],
    responses={
        403: {"model": ErrorResponse, "description": "Forbidden"},
        404: {"model": ErrorResponse, "description": "Not Found"},
    },
)


@router.post(
    "",
    response_model=StudyResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_study(
    request: StudyCreateRequest,
    db: Session = Depends(get_db),
    current_user: TokenPayload = Depends(require_any_role([Role.ADMIN, Role.RESEARCHER])),
) -> Study:
    study = Study(
        name=request.name,
        description=request.description,
        osf_registration_url=request.osf_registration_url,
    )
    db.add(study)
    db.commit()
    db.refresh(study)

    log_audit_event(
        db=db,
        user_id=current_user.user_id,
        action="study:create",
        resource_type="study",
        resource_id=study.study_id,  # type: ignore[arg-type]
    )
    return study


@router.get("", response_model=List[StudyResponse])
async def list_studies(db: Session = Depends(get_db)) -> List[Study]:
    return list(db.query(Study).order_by(Study.created_at.desc()).all())


@router.get("/{study_id}", response_model=StudyResponse)
async def get_study(study_id: str, db: Session = Depends(get_db)) -> Study:
    study = db.query(Study).filter(Study.study_id == study_id).first()
    if study is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Study not found")
    return study


@router.get(
    "/{study_id}/exclusion-report",
    response_model=ExclusionReportResponse,
    summary="Exclusion rates for the dataset card",
)
async def get_exclusion_report(
    study_id: str,
    battery_id: Optional[str] = None,
    db: Session = Depends(get_db),
    _current_user: TokenPayload = Depends(require_any_role([Role.ADMIN, Role.RESEARCHER])),
) -> dict[str, Any]:
    if db.query(Study).filter(Study.study_id == study_id).first() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Study not found")
    return exclusion_report(db, study_id, battery_id)

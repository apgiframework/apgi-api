"""Study-scoped staff access (NEW-16): admins reach every study; researchers only
the studies they are members of. A bare RESEARCHER role is not enough."""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.database.models import Participant, StudyEnrollment, StudyMember
from app.services.authorization import Role, has_any_role


def is_study_member(db: Session, user_id: str, study_id: str) -> bool:
    return (
        db.query(StudyMember)
        .filter(StudyMember.user_id == user_id, StudyMember.study_id == study_id)
        .first()
        is not None
    )


def add_study_member(db: Session, study_id: str, user_id: str) -> None:
    if not is_study_member(db, user_id, study_id):
        db.add(StudyMember(study_id=study_id, user_id=user_id))
        db.commit()


def can_access_study(db: Session, current_user, study_id: str) -> bool:
    if has_any_role(current_user.roles, [Role.ADMIN]):
        return True
    return has_any_role(current_user.roles, [Role.RESEARCHER]) and is_study_member(
        db, current_user.user_id, study_id
    )


def require_study_access(db: Session, current_user, study_id: str) -> None:
    if not can_access_study(db, current_user, study_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not a member of this study"
        )


def can_access_participant(db: Session, current_user, participant: Participant) -> bool:
    """Admin; the participant's owning account; or a researcher who is a member of a
    study the participant is enrolled in."""
    if has_any_role(current_user.roles, [Role.ADMIN]):
        return True
    if participant.user_id and participant.user_id == current_user.user_id:
        return True
    if not has_any_role(current_user.roles, [Role.RESEARCHER]):
        return False
    return (
        db.query(StudyEnrollment)
        .join(StudyMember, StudyMember.study_id == StudyEnrollment.study_id)
        .filter(
            StudyEnrollment.participant_id == participant.participant_id,
            StudyMember.user_id == current_user.user_id,
        )
        .first()
        is not None
    )

"""
Consent enforcement (governing doc §7.1): independently versioned consent
purposes — PRODUCT_USE, RESEARCH_PARTICIPATION, DATA_SHARING and RECONTACT —
gate every action that touches a participant's data. Granting one never
implies another.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.database.models import Consent, ConsentRecord, ConsentType


def hash_consent_text(text_shown: str) -> str:
    """SHA-256 of the exact consent text a participant was shown, for audit."""
    return hashlib.sha256(text_shown.encode("utf-8")).hexdigest()


def record_consent(
    db: Session,
    participant_id: str,
    consent_type: ConsentType,
    consent_text: str,
    ip_address: Optional[str] = None,
    version: Optional[str] = None,
) -> Consent:
    """Record a new consent grant. Does not revoke any prior grant of a
    different type — the two consent types are independent."""
    consent = Consent(
        participant_id=participant_id,
        consent_type=consent_type,
        version=version or settings.consent_current_versions[consent_type.value],
        consent_text_hash=hash_consent_text(consent_text),
        ip_address=ip_address,
    )
    db.add(consent)
    db.add(
        ConsentRecord(
            participant_id=participant_id,
            consent_type=consent_type.value,
            action="grant",
            version=consent.version,
            consent_text_hash=consent.consent_text_hash,
        )
    )
    db.commit()
    db.refresh(consent)
    return consent


def revoke_consent(db: Session, participant_id: str, consent_type: ConsentType) -> int:
    """Close every open grant of `consent_type`; returns how many were closed.

    Closing all of them (not just the latest) guarantees an older grant can
    never become the effective one again after a revoke.
    """
    now = datetime.now(timezone.utc)
    open_grants = (
        db.query(Consent)
        .filter(
            Consent.participant_id == participant_id,
            Consent.consent_type == consent_type,
            Consent.revoked_at.is_(None),
        )
        .all()
    )
    for grant in open_grants:
        grant.revoked_at = now  # type: ignore[assignment]
    if open_grants:
        db.add(
            ConsentRecord(
                participant_id=participant_id, consent_type=consent_type.value, action="withdraw"
            )
        )
        db.commit()
    return len(open_grants)


def _effective_consent(
    db: Session, participant_id: str, consent_type: ConsentType
) -> Optional[Consent]:
    return (
        db.query(Consent)
        .filter(
            Consent.participant_id == participant_id,
            Consent.consent_type == consent_type,
            Consent.revoked_at.is_(None),
        )
        .order_by(Consent.granted_at.desc())
        .first()
    )


def has_active_consent(
    db: Session,
    participant_id: str,
    consent_type: ConsentType,
    require_current_version: bool = True,
) -> bool:
    """Whether the participant has an unrevoked grant of `consent_type`.

    When `require_current_version`, a grant of an older version than
    `settings.consent_current_versions` does not count — the consent text
    changed, so it must be re-granted before continuing.
    """
    current = _effective_consent(db, participant_id, consent_type)
    if current is None:
        return False
    if require_current_version:
        required_version = settings.consent_current_versions[consent_type.value]
        if current.version != required_version:
            return False
    return True


def require_consent(
    db: Session,
    participant_id: str,
    *consent_types: ConsentType,
    require_current_version: bool = True,
) -> None:
    """Raise 403 unless every listed consent type is currently active.

    Call this before any endpoint reads/writes participant data gated by
    consent (trial ingestion, scoring, export, report access).
    """
    missing = [
        ct.value
        for ct in consent_types
        if not has_active_consent(db, participant_id, ct, require_current_version)
    ]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Missing or outdated consent: {', '.join(missing)}",
        )


def require_any_consent(
    db: Session,
    participant_id: str,
    *consent_types: ConsentType,
    require_current_version: bool = True,
) -> None:
    """Raise 403 unless at least one listed consent type is currently active.

    Lets the product run on PRODUCT_USE alone: research participation is an
    additional, independent purpose, not a prerequisite for using the product.
    """
    if any(
        has_active_consent(db, participant_id, ct, require_current_version) for ct in consent_types
    ):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"Missing or outdated consent: one of {', '.join(ct.value for ct in consent_types)}",
    )

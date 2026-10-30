"""
GET /v1/meta and GET /v1/dataset-card — generated live from identifiers.yaml
(the single source of truth; see identifiers.yaml's own header comment and
scripts/generate_identifiers.py, which generates the static copies of the
same data for README.md/CITATION.cff/.zenodo.json).

Both endpoints are unauthenticated: they carry no participant data, only
project/citation metadata and study-level counts, and are meant to be
fetched by the public site footer and by external tooling (dataset
catalogues, citation managers) without requiring a token.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.database.models import (
    Battery,
    Participant,
    ParticipantSession,
    ParticipantSessionStatus,
    Study,
)
from app.schemas.reporting import DatasetCardResponse, MetaResponse
from app.services.identifiers import load_identifiers
from app.services.version_lattice import build_stamps

router = APIRouter(tags=["Meta"])


@router.get("/v1/meta", response_model=MetaResponse)
async def get_meta() -> MetaResponse:
    ids = load_identifiers()
    return MetaResponse(
        researcher=ids["researcher"],
        osf=ids["osf"],
        zenodo=ids["zenodo"],
        code=ids["code"],
        web=ids["web"],
        licences=ids["licences"],
        release_state=ids["release_state"],
        ethics_number=ids["ethics"].get("approval_number"),
        version_lattice=_lattice_snapshot(),
    )


def _lattice_snapshot() -> dict[str, Any]:
    """Server-side lattice values (same source as the stamps on every result)."""
    stamps = build_stamps(battery_version=ids_battery_version())
    return {k: v for k, v in stamps.items() if v is not None}


def ids_battery_version() -> Optional[str]:
    value = load_identifiers()["release_state"].get("battery_version")
    return str(value) if value is not None else None


@router.get("/v1/dataset-card", response_model=DatasetCardResponse)
async def get_dataset_card(db: Session = Depends(get_db)) -> DatasetCardResponse:
    ids = load_identifiers()
    ethics = ids["ethics"]

    warnings = []
    if not ethics.get("privacy_review_completed"):
        warnings.append("Privacy review not yet completed (ethics.privacy_review_completed=false).")
    if not ethics.get("reviewer"):
        warnings.append("No ethics reviewer of record (ethics.reviewer is unset).")
    if ids["release_state"]["current"] != "calibrated":
        warnings.append(
            f"release_state is '{ids['release_state']['current']}' — this dataset has not cleared "
            "the K1-K3 calibration gates."
        )

    counts = {
        "studies": db.query(Study).count(),
        "batteries": db.query(Battery).count(),
        "participants": db.query(Participant).filter(Participant.is_deleted.is_(False)).count(),
        "completed_sessions": db.query(ParticipantSession)
        .filter(ParticipantSession.status == ParticipantSessionStatus.COMPLETED)
        .count(),
    }

    return DatasetCardResponse(
        release_state=ids["release_state"],
        licence=ids["licences"]["reference_dataset"],
        ethics=ethics,
        counts=counts,
        warnings=warnings,
    )


_NOT_RECORDED = {"status": "not_recorded"}
_CARD_FIELDS = (
    "recruitment_channels",
    "countries_and_languages",
    "demographic_distributions",
    "missingness_and_dropout",
    "incentives",
    "known_sampling_bias",
    "convenience_vs_panel_comparison",
    "diagnostic_instruments_excluded",
)


@router.get("/v1/reference-datasets/{version}/card")
async def get_reference_dataset_card(version: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Immutable card for one released reference dataset version.

    404 until identifiers.yaml records that version as released. Fields the
    project has not yet recorded are reported as ``not_recorded`` rather than
    omitted or invented.
    """
    ids = load_identifiers()
    released = ids["release_state"].get("reference_dataset_version")
    if released is None or str(released) != version:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"No reference dataset version '{version}' has been released",
        )
    ethics = ids["ethics"]
    card: dict[str, Any] = {
        "reference_dataset_version": version,
        "release_state": ids["release_state"],
        "licence": ids["licences"]["reference_dataset"],
        "ethics": ethics,
        "task_and_feature_versions": {
            "battery_version": ids["release_state"].get("battery_version"),
            "feature_version": ids["release_state"].get("feature_version"),
            "model_version": ids["release_state"].get("model_version"),
        },
        "inclusion_and_exclusion_rules": "see GET /v1/dataset-card exclusion counts",
        "sample": "provisional reference sample, not a population norm",
    }
    for field in _CARD_FIELDS:
        card[field] = dict(_NOT_RECORDED)
    return card

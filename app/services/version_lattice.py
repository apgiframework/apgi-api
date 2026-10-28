"""Version-lattice stamps (S1-CON-03).

Every result carries the full set of independent version identifiers it was
produced under, so a protocol upgrade never forces a new API path and vice
versa. `validate_stamps` is called at write time; a result missing a
mandatory stamp is rejected rather than stored.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app import __version__
from app.config import settings
from app.modules.scoring.exposure import ExposureGate

API_CONTRACT = "v1"
SCHEMA_VERSION = "1.0.0"
TIER_CONFIG_VERSION = "1.0.0"
SCORING_SPEC_VERSION = "1.0.0"
PREPROCESSING_VERSION = "1.0.0"
QUALITY_RULES_VERSION = "1.0.0"
FEATURE_VERSION = "1.0.0"

# Present on every result and never null.
MANDATORY_STAMPS = (
    "platform_release",
    "api_contract",
    "schema_version",
    "battery_version",
    "feature_version",
    "gate_table_version",
    "tier_config_version",
    "consent_version",
    "scoring_spec_version",
    "preprocessing_version",
    "quality_rules_version",
)

# Present on every result but null where the result has no such provenance
# (e.g. no model bound, no reference dataset yet, not instrument-based).
NULLABLE_STAMPS = (
    "protocol_id",
    "protocol_version",
    "protocol_sha256",
    "instrument_id",
    "instrument_version",
    "instrument_sha256",
    "model_id",
    "model_version",
    "reference_dataset_version",
    "client_build",
    "analysis_manifest_sha256",
)

ALL_STAMPS = MANDATORY_STAMPS + NULLABLE_STAMPS


class StampError(ValueError):
    """A result is missing a mandatory version stamp."""


def validate_stamps(stamps: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(stamps, dict):
        raise StampError("Result has no version stamps")
    missing = [k for k in MANDATORY_STAMPS if not stamps.get(k)]
    absent = [k for k in NULLABLE_STAMPS if k not in stamps]
    if missing or absent:
        raise StampError(f"Missing version stamps: {sorted(missing + absent)}")
    return stamps


def build_stamps(
    battery_version: Optional[str],
    model_id: Optional[str] = None,
    model_version: Optional[str] = None,
    feature_version: Optional[str] = None,
    **provenance: Optional[str],
) -> Dict[str, Any]:
    """Assemble the stamps the server knows; `provenance` fills nullable ones."""
    unknown = set(provenance) - set(NULLABLE_STAMPS)
    if unknown:
        raise StampError(f"Unknown stamps: {sorted(unknown)}")
    stamps: Dict[str, Any] = {k: None for k in NULLABLE_STAMPS}
    stamps.update(provenance)
    stamps.update(
        platform_release=__version__,
        api_contract=API_CONTRACT,
        schema_version=SCHEMA_VERSION,
        battery_version=battery_version,
        feature_version=feature_version or FEATURE_VERSION,
        gate_table_version=ExposureGate().version,
        tier_config_version=TIER_CONFIG_VERSION,
        consent_version=dict(settings.consent_current_versions),
        scoring_spec_version=SCORING_SPEC_VERSION,
        preprocessing_version=PREPROCESSING_VERSION,
        quality_rules_version=QUALITY_RULES_VERSION,
        model_id=model_id,
        model_version=model_version,
    )
    return stamps

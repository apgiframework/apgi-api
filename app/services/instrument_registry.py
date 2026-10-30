"""Versioned instrument registry (S3-INS-01).

An instrument version is addressed by (instrument_id, version) and pinned by
the SHA-256 of its canonical item bank. Responses are only accepted against a
registered version whose hash matches what the client was served, so a client
can never answer against a bank the server did not issue. The Explorer-35 bank
is the only registered instrument today; new ones register here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from app.services import explorer_contract as explorer


@dataclass(frozen=True)
class InstrumentVersion:
    instrument_id: str
    version: str
    sha256: str
    bank: dict[str, Any]
    # Returns an error message for an invalid {item_id, value} answer, else None.
    validate_answer: Callable[[int, int], Optional[str]]


def _explorer_version() -> InstrumentVersion:
    bank = explorer.item_bank()

    def validate(item_id: int, value: int) -> Optional[str]:
        return explorer.validate_payload(
            {"item_id": item_id, "value": value, "instrument_sha256": bank["sha256"]}
        )

    return InstrumentVersion(
        instrument_id=explorer.INSTRUMENT_ID,
        version=str(bank["bank_version"]),
        sha256=str(bank["sha256"]),
        bank=bank,
        validate_answer=validate,
    )


def registered_versions() -> list[InstrumentVersion]:
    return [_explorer_version()]


def get_version(instrument_id: str, version: str) -> Optional[InstrumentVersion]:
    for entry in registered_versions():
        if entry.instrument_id == instrument_id and entry.version == version:
            return entry
    return None

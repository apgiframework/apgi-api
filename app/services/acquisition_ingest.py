"""Three-stage chunk ingest (init -> direct bucket PUT -> commit -> SHA-256 job).

Gigabytes never pass through the API process. Chunk status:

``accepted``     SHA-256 recomputed from the stored bytes equals the manifest.
``quarantined``  stored but not trusted yet: CRC32C mismatch, or a sequence gap (a chunk held
                 for a gap is re-examined once its predecessors are accepted).
``rejected``     unusable: object missing, wrong size, duplicate sequence, SHA-256 mismatch.
"""

from __future__ import annotations

import hashlib
import re
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.database.models import AcquisitionUpload
from app.services.acquisition_storage import AcquisitionObjectStore

PENDING, ACCEPTED, QUARANTINED, REJECTED = "pending", "accepted", "quarantined", "rejected"
GAP_REASON = "sequence gap: earlier chunk not accepted yet"
_SEGMENT = re.compile(r"(?:^|/)segment-(\d+)(?:\.[A-Za-z0-9]+)?$")

_CRC_TABLE: List[int] = []
for _i in range(256):
    _c = _i
    for _ in range(8):
        _c = (_c >> 1) ^ 0x82F63B78 if _c & 1 else _c >> 1
    _CRC_TABLE.append(_c)


def crc32c(data: bytes) -> int:
    """CRC32C (Castagnoli); uses ``google_crc32c`` when installed."""
    try:
        import google_crc32c  # type: ignore[import-not-found]

        return int(google_crc32c.value(data))
    except ImportError:
        crc = 0xFFFFFFFF
        for b in data:
            crc = _CRC_TABLE[(crc ^ b) & 0xFF] ^ (crc >> 8)
        return crc ^ 0xFFFFFFFF


class UploadForbidden(Exception):
    """The principal may not use this acquisition session."""


class UnknownUpload(Exception):
    """No upload with this id."""


class Conflict(Exception):
    """The request contradicts an already accepted object."""


def sequence_of(object_name: str, explicit: Optional[int]) -> Optional[int]:
    """Chunk sequence: the explicit value, else parsed from ``segment-NNNNNN``."""
    if explicit is not None:
        return explicit
    m = _SEGMENT.search(object_name)
    return int(m.group(1)) if m else None


def key_of(rec: AcquisitionUpload) -> str:
    return f"{rec.session_id}/{rec.object_name}"


def _owner(db: Session, session_id: str) -> Optional[str]:
    first = (
        db.query(AcquisitionUpload)
        .filter(AcquisitionUpload.session_id == session_id)
        .order_by(AcquisitionUpload.created_at, AcquisitionUpload.upload_id)
        .first()
    )
    return None if first is None else str(first.principal)


def authorize(db: Session, principal: str, session_id: str, *, is_admin: bool) -> None:
    """The first uploader owns a session; others need the admin role."""
    owner = _owner(db, session_id)
    if owner is not None and owner != principal and not is_admin:
        raise UploadForbidden(f"not allowed to upload to session {session_id!r}")


def _siblings(db: Session, rec: AcquisitionUpload) -> List[AcquisitionUpload]:
    return (
        db.query(AcquisitionUpload)
        .filter(
            AcquisitionUpload.session_id == rec.session_id,
            AcquisitionUpload.sequence.isnot(None),
            AcquisitionUpload.upload_id != rec.upload_id,
        )
        .all()
    )


def _sequence_taken(db: Session, rec: AcquisitionUpload) -> bool:
    return any(
        r.sequence == rec.sequence and r.committed and r.status in (PENDING, ACCEPTED)
        for r in _siblings(db, rec)
    )


def _predecessors_accepted(db: Session, rec: AcquisitionUpload) -> bool:
    accepted = {r.sequence for r in _siblings(db, rec) if r.status in (ACCEPTED, PENDING)}
    return all(i in accepted for i in range(int(rec.sequence or 0)))


def _set(db: Session, rec: AcquisitionUpload, status: str, reason: str = "") -> AcquisitionUpload:
    rec.status, rec.reason = status, reason  # type: ignore[assignment]
    db.commit()
    return rec


def init_upload(
    db: Session,
    store: AcquisitionObjectStore,
    *,
    principal: str,
    is_admin: bool,
    session_id: str,
    object_name: str,
    size_bytes: int,
    crc32c_value: int,
    sha256: str,
    sequence: Optional[int],
    url_ttl_s: int,
) -> Dict[str, object]:
    """Stage A: register the manifest and return a signed upload URL."""
    authorize(db, principal, session_id, is_admin=is_admin)
    accepted = (
        db.query(AcquisitionUpload)
        .filter(
            AcquisitionUpload.session_id == session_id,
            AcquisitionUpload.object_name == object_name,
            AcquisitionUpload.status == ACCEPTED,
        )
        .first()
    )
    if accepted is not None:
        if accepted.sha256 != sha256:
            raise Conflict("an accepted object with different content already exists")
        return {
            "upload_id": accepted.upload_id,
            "signed_url": "",
            "expires_in_s": 0,
            "already_accepted": True,
        }
    rec = AcquisitionUpload(
        session_id=session_id,
        object_name=object_name,
        size_bytes=size_bytes,
        crc32c=crc32c_value,
        sha256=sha256.lower(),
        sequence=sequence_of(object_name, sequence),
        principal=principal,
        status=PENDING,
        reason="",
        committed=False,
    )
    db.add(rec)
    db.commit()
    url = store.signed_upload_url(key_of(rec), size_bytes=size_bytes, expires_in_s=url_ttl_s)
    return {
        "upload_id": rec.upload_id,
        "signed_url": url,
        "expires_in_s": url_ttl_s,
        "already_accepted": False,
    }


def get_upload(db: Session, upload_id: str) -> AcquisitionUpload:
    rec = db.get(AcquisitionUpload, upload_id)
    if rec is None:
        raise UnknownUpload(upload_id)
    return rec


def commit(
    db: Session,
    store: AcquisitionObjectStore,
    *,
    upload_id: str,
    principal: str,
    is_admin: bool,
) -> AcquisitionUpload:
    """Stage C: check existence, size, sequence and CRC32C; queue SHA-256 verification.

    Returns the record; ``status == "pending"`` means a verification job must be enqueued.
    """
    rec = get_upload(db, upload_id)
    authorize(db, principal, str(rec.session_id), is_admin=is_admin)
    if principal != rec.principal and not is_admin:
        raise UploadForbidden("upload was initialised by a different principal")
    if rec.committed:
        return rec
    rec.committed = True  # type: ignore[assignment]
    key = key_of(rec)
    size = store.size(key)
    if size is None:
        return _set(db, rec, REJECTED, "object does not exist in the bucket")
    if size != rec.size_bytes:
        return _set(db, rec, REJECTED, f"size {size} != manifest {rec.size_bytes}")
    if rec.sequence is not None and _sequence_taken(db, rec):
        return _set(db, rec, REJECTED, f"duplicate sequence {rec.sequence}")
    if crc32c(store.read(key)) != rec.crc32c:
        return _set(db, rec, QUARANTINED, "CRC32C mismatch")
    if rec.sequence is not None and not _predecessors_accepted(db, rec):
        return _set(db, rec, QUARANTINED, GAP_REASON)
    return _set(db, rec, PENDING, "")


def verify(db: Session, store: AcquisitionObjectStore, upload_id: str) -> str:
    """Verification job: recompute SHA-256, then release gap-quarantined successors.

    Returns the final status of ``upload_id``.
    """
    rec = get_upload(db, upload_id)
    if rec.status == PENDING and rec.committed:
        digest = hashlib.sha256(store.read(key_of(rec))).hexdigest()
        if digest == rec.sha256:
            _set(db, rec, ACCEPTED)
        else:
            _set(db, rec, REJECTED, "SHA-256 mismatch")
    for held in release_gap_quarantined(db, str(rec.session_id)):
        verify(db, store, str(held.upload_id))
    return str(rec.status)


def release_gap_quarantined(db: Session, session_id: str) -> List[AcquisitionUpload]:
    """Move chunks held for a sequence gap back to pending once the gap is closed."""
    released: List[AcquisitionUpload] = []
    held = (
        db.query(AcquisitionUpload)
        .filter(
            AcquisitionUpload.session_id == session_id,
            AcquisitionUpload.status == QUARANTINED,
            AcquisitionUpload.reason == GAP_REASON,
        )
        .all()
    )
    for rec in held:
        if _predecessors_accepted(db, rec):
            _set(db, rec, PENDING, "")
            released.append(rec)
    return released


def status_for_key(
    db: Session, key: str, principal: str, *, is_admin: bool
) -> Optional[AcquisitionUpload]:
    """Latest upload for ``<session_id>/<object_name>``; ``None`` if unknown."""
    session_id, _, object_name = key.partition("/")
    rec = (
        db.query(AcquisitionUpload)
        .filter(
            AcquisitionUpload.session_id == session_id,
            AcquisitionUpload.object_name == object_name,
        )
        .order_by(AcquisitionUpload.created_at.desc(), AcquisitionUpload.upload_id)
        .first()
    )
    if rec is None:
        return None
    authorize(db, principal, session_id, is_admin=is_admin)
    return rec

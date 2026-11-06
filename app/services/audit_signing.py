"""
Tamper-evident signing for audit log entries (governing doc §7.4).

Score generation and report-access events are the ones the plan calls out
by name, but every audit entry is signed uniformly — there is no reliable
way to guarantee only "sensitive" actions ever get logged as such, and an
unsigned subset would just be an unverified subset. HMAC-SHA256 over the
canonical fields means altering `details`, `status`, or the actor after the
fact is detectable without needing a separate immutable log store.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from app.config import settings


class AuditSigningNotConfiguredError(RuntimeError):
    pass


CANONICAL_LEGACY = 1
CANONICAL_BOUND = 2
GENESIS_HASH = "0" * 64


def normalize_timestamp(value: Any) -> Optional[str]:
    """Canonical UTC text for a timestamp; tz-naive values are taken as UTC
    (SQLite drops tzinfo on round-trip)."""
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.isoformat(timespec="microseconds")


def get_signing_key(version: Optional[str]) -> Optional[str]:
    """Key for `version`: the registered historical key, or the current key when
    `version` is the current version label (or no version is given)."""
    if version is None or version == settings.audit_signing_key_version:
        return settings.audit_signing_key
    return (settings.audit_signing_keys or {}).get(version)


def _canonical_payload(
    audit_id: str,
    user_id: Optional[str],
    action: Optional[str],
    resource_type: Optional[str],
    resource_id: Optional[str],
    status: str,
    details: Optional[dict[str, Any]],
    prev_record_sha256: Optional[str] = None,
    *,
    canonical_version: int = CANONICAL_LEGACY,
    timestamp: Any = None,
    chain_seq: Optional[int] = None,
    signing_key_version: Optional[str] = None,
) -> bytes:
    # sort_keys=True makes this deterministic regardless of dict insertion order.
    payload: dict[str, Any] = {
        "audit_id": audit_id,
        "user_id": user_id,
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "status": status,
        "details": details or {},
    }
    # Only chained rows bind their predecessor, so legacy (pre-chain)
    # signatures keep verifying unchanged.
    if prev_record_sha256 is not None:
        payload["prev_record_sha256"] = prev_record_sha256
    if canonical_version >= CANONICAL_BOUND:
        # Metadata that used to be unsigned: altering any of it breaks verification.
        payload["canonical_version"] = canonical_version
        payload["timestamp"] = normalize_timestamp(timestamp)
        payload["chain_seq"] = chain_seq
        payload["signing_key_version"] = signing_key_version
    return json.dumps(payload, sort_keys=True, default=str).encode("utf-8")


def sign_audit_entry(
    audit_id: str,
    user_id: Optional[str],
    action: Optional[str],
    resource_type: Optional[str],
    resource_id: Optional[str],
    status: str,
    details: Optional[dict[str, Any]],
    prev_record_sha256: Optional[str] = None,
    *,
    canonical_version: int = CANONICAL_LEGACY,
    timestamp: Any = None,
    chain_seq: Optional[int] = None,
    signing_key_version: Optional[str] = None,
) -> str:
    key = get_signing_key(signing_key_version)
    if not key:
        raise AuditSigningNotConfiguredError(
            "AUDIT_SIGNING_KEY is not configured (or has no key for version "
            f"{signing_key_version!r}). Set it before writing audit log entries."
        )
    payload = _canonical_payload(
        audit_id,
        user_id,
        action,
        resource_type,
        resource_id,
        status,
        details,
        prev_record_sha256,
        canonical_version=canonical_version,
        timestamp=timestamp,
        chain_seq=chain_seq,
        signing_key_version=signing_key_version,
    )
    return hmac.new(key.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def verify_audit_entry(
    audit_id: str,
    user_id: Optional[str],
    action: Optional[str],
    resource_type: Optional[str],
    resource_id: Optional[str],
    status: str,
    details: Optional[dict[str, Any]],
    signature: Optional[str],
    prev_record_sha256: Optional[str] = None,
    *,
    canonical_version: int = CANONICAL_LEGACY,
    timestamp: Any = None,
    chain_seq: Optional[int] = None,
    signing_key_version: Optional[str] = None,
) -> bool:
    """True iff `signature` matches the entry's fields under the key named by
    `signing_key_version`. A NULL signature is never valid."""
    if not signature or not get_signing_key(signing_key_version):
        return False
    expected = sign_audit_entry(
        audit_id,
        user_id,
        action,
        resource_type,
        resource_id,
        status,
        details,
        prev_record_sha256,
        canonical_version=canonical_version,
        timestamp=timestamp,
        chain_seq=chain_seq,
        signing_key_version=signing_key_version,
    )
    return hmac.compare_digest(expected, signature)


def _row_fields(row: Any) -> dict[str, Any]:
    version = getattr(row, "canonical_version", None) or CANONICAL_LEGACY
    actor = getattr(row, "actor_id", None) if version >= CANONICAL_BOUND else row.user_id
    return {
        "audit_id": row.audit_id,
        "user_id": actor,
        "action": row.action,
        "resource_type": row.resource_type,
        "resource_id": row.resource_id,
        "status": row.status,
        "details": row.details,
        "prev_record_sha256": row.prev_record_sha256,
        "canonical_version": version,
        "timestamp": getattr(row, "timestamp", None),
        "chain_seq": row.chain_seq,
        "signing_key_version": row.signing_key_version,
    }


def record_sha256(
    audit_id: str,
    user_id: Optional[str],
    action: Optional[str],
    resource_type: Optional[str],
    resource_id: Optional[str],
    status: str,
    details: Optional[dict[str, Any]],
    prev_record_sha256: str,
    signature: Optional[str],
    *,
    canonical_version: int = CANONICAL_LEGACY,
    timestamp: Any = None,
    chain_seq: Optional[int] = None,
    signing_key_version: Optional[str] = None,
) -> str:
    """SHA-256 of a record's canonical fields plus its signature. The next
    record stores this as `prev_record_sha256`, forming the chain."""
    payload = _canonical_payload(
        audit_id,
        user_id,
        action,
        resource_type,
        resource_id,
        status,
        details,
        prev_record_sha256,
        canonical_version=canonical_version,
        timestamp=timestamp,
        chain_seq=chain_seq,
        signing_key_version=signing_key_version,
    )
    return hashlib.sha256(payload + (signature or "").encode("utf-8")).hexdigest()


def record_sha256_of_row(row: Any) -> str:
    f = _row_fields(row)
    return record_sha256(**f, signature=row.signature)


def verify_chain(rows: Iterable[Any]) -> tuple[bool, Optional[str]]:
    """Verify an ordered (by chain_seq) iterable of chained AuditLog rows.

    Returns (True, None) when every signature verifies under the row's own key
    version, every row links to its predecessor and chain_seq is contiguous from 1;
    otherwise (False, audit_id_of_first_broken_row). Legacy rows without a
    chain_seq are skipped.
    """
    prev = GENESIS_HASH
    expected_seq = 1
    for row in rows:
        if row.chain_seq is None:
            continue
        if row.chain_seq != expected_seq or row.prev_record_sha256 != prev:
            return False, row.audit_id
        f = _row_fields(row)
        if not verify_audit_entry(**f, signature=row.signature):
            return False, row.audit_id
        prev = record_sha256(**f, signature=row.signature)
        expected_seq += 1
    return True, None

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
from typing import Any, Iterable, Optional

from app.config import settings


class AuditSigningNotConfiguredError(RuntimeError):
    pass


def _canonical_payload(
    audit_id: str,
    user_id: Optional[str],
    action: Optional[str],
    resource_type: Optional[str],
    resource_id: Optional[str],
    status: str,
    details: Optional[dict[str, Any]],
    prev_record_sha256: Optional[str] = None,
) -> bytes:
    # sort_keys=True makes this deterministic regardless of dict insertion order.
    payload = {
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
) -> str:
    if not settings.audit_signing_key:
        raise AuditSigningNotConfiguredError(
            "AUDIT_SIGNING_KEY is not configured. Set it before writing audit log entries."
        )
    payload = _canonical_payload(
        audit_id, user_id, action, resource_type, resource_id, status, details, prev_record_sha256
    )
    return hmac.new(settings.audit_signing_key.encode("utf-8"), payload, hashlib.sha256).hexdigest()


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
) -> bool:
    """True iff `signature` matches the entry's fields. A NULL signature
    (legacy, pre-signing rows) is never "valid" — callers that need to
    distinguish "legacy" from "tampered" should check `signature is None`
    themselves before calling this."""
    if not signature or not settings.audit_signing_key:
        return False
    expected = sign_audit_entry(
        audit_id, user_id, action, resource_type, resource_id, status, details, prev_record_sha256
    )
    return hmac.compare_digest(expected, signature)


GENESIS_HASH = "0" * 64


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
) -> str:
    """SHA-256 of a record's canonical fields plus its signature. The next
    record stores this as `prev_record_sha256`, forming the chain."""
    payload = _canonical_payload(
        audit_id, user_id, action, resource_type, resource_id, status, details, prev_record_sha256
    )
    return hashlib.sha256(payload + (signature or "").encode("utf-8")).hexdigest()


def verify_chain(rows: Iterable[Any]) -> tuple[bool, Optional[str]]:
    """Verify an ordered (by chain_seq) iterable of chained AuditLog rows.

    Returns (True, None) when every signature verifies and every row links to
    its predecessor; otherwise (False, audit_id_of_first_broken_row). Legacy
    rows without a chain_seq are skipped.
    """
    prev = GENESIS_HASH
    for row in rows:
        if row.chain_seq is None:
            continue
        if row.prev_record_sha256 != prev:
            return False, row.audit_id
        if not verify_audit_entry(
            row.audit_id,
            row.user_id,
            row.action,
            row.resource_type,
            row.resource_id,
            row.status,
            row.details,
            row.signature,
            row.prev_record_sha256,
        ):
            return False, row.audit_id
        prev = record_sha256(
            row.audit_id,
            row.user_id,
            row.action,
            row.resource_type,
            row.resource_id,
            row.status,
            row.details,
            row.prev_record_sha256,
            row.signature,
        )
    return True, None

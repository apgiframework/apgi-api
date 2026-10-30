"""
Signed update feed for the APGI Appendix macOS client (``GET /updates.json``).

Contract (mirrors ``UpdateSignatureVerifier.swift`` in APGI-Appendix):

* The signed payload is the manifest fields ``latestVersion``, ``releaseNotes`` and,
  when present, ``minimumVersion``, ``downloadURL``, ``releaseDate``, ``isCritical``.
  ``signature``, ``keyId`` and ``algorithm`` are never part of the payload.
* Canonical bytes: UTF-8 JSON, keys sorted, no insignificant whitespace, ``/`` not
  escaped (Swift ``JSONSerialization`` with ``.sortedKeys`` + ``.withoutEscapingSlashes``).
* Algorithm ``P256-ECDSA``: ECDSA over SHA-256 of the canonical bytes, signature encoded
  as the raw 64-byte ``r || s`` form (CryptoKit ``rawRepresentation``), then base64.
* ``keyId`` names a public key the client has pinned. Rotating a key means shipping a
  client that pins the new id; the server never ships key material.

The feed fails closed: with no signing key or no manifest configured the endpoint
answers 503 rather than serving an unsigned document.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Dict, Optional

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)

ALGORITHM = "P256-ECDSA"
REQUIRED_FIELDS = ("latestVersion", "releaseNotes")
OPTIONAL_FIELDS = ("minimumVersion", "downloadURL", "releaseDate", "isCritical")
_STRING_FIELDS = ("latestVersion", "releaseNotes", "minimumVersion", "downloadURL", "releaseDate")


class UpdateFeedError(Exception):
    """The feed cannot be served as a correctly signed document."""


def validate_manifest(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Return only the signed fields, rejecting anything the client could not verify."""
    for name in REQUIRED_FIELDS:
        if not isinstance(manifest.get(name), str) or not manifest[name]:
            raise UpdateFeedError(f"manifest field {name!r} is required")
    payload = {
        k: manifest[k] for k in REQUIRED_FIELDS + OPTIONAL_FIELDS if manifest.get(k) is not None
    }
    for name in _STRING_FIELDS:
        if name in payload and not isinstance(payload[name], str):
            raise UpdateFeedError(f"manifest field {name!r} must be a string")
    if "isCritical" in payload and not isinstance(payload["isCritical"], bool):
        raise UpdateFeedError("manifest field 'isCritical' must be a boolean")
    if payload.get("isCritical") and "minimumVersion" not in payload:
        # The client rejects critical updates that omit minimumVersion.
        raise UpdateFeedError("critical updates require minimumVersion")
    return payload


def canonical_bytes(payload: Dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def sign_payload(payload: Dict[str, Any], private_key: ec.EllipticCurvePrivateKey) -> str:
    der = private_key.sign(canonical_bytes(payload), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return base64.b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big")).decode("ascii")


def verify_signature(
    payload: Dict[str, Any], signature_b64: str, public_key: ec.EllipticCurvePublicKey
) -> bool:
    """Reference verifier used by tests; the shipped client implements its own."""
    raw = base64.b64decode(signature_b64)
    if len(raw) != 64:
        return False
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    try:
        public_key.verify(der, canonical_bytes(payload), ec.ECDSA(hashes.SHA256()))
    except Exception:
        return False
    return True


def load_private_key(pem: str) -> ec.EllipticCurvePrivateKey:
    key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise UpdateFeedError("update signing key must be a P-256 EC private key")
    return key


def build_feed(
    manifest_path: Optional[str], private_key_pem: Optional[str], key_id: Optional[str]
) -> Dict[str, Any]:
    if not (manifest_path and private_key_pem and key_id):
        raise UpdateFeedError("update feed is not configured")
    try:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UpdateFeedError(f"update manifest unreadable: {exc}") from exc
    if not isinstance(manifest, dict):
        raise UpdateFeedError("update manifest must be a JSON object")
    payload = validate_manifest(manifest)
    signature = sign_payload(payload, load_private_key(private_key_pem))
    return {**payload, "signature": signature, "keyId": key_id, "algorithm": ALGORITHM}

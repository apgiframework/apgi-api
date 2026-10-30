"""Signed update feed: canonical payload, signature format, fail-closed endpoint."""

import base64
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from starlette.testclient import TestClient

from app.config import settings
from app.main import create_app
from app.services import update_feed as uf

MANIFEST = {
    "latestVersion": "2.1.0",
    "releaseNotes": "Fixes — résumé",
    "minimumVersion": "2.0.0",
    "downloadURL": "https://apgiframework.com/downloads/APGI-Appendix-2.1.0.zip",
    "releaseDate": "2026-11-01",
    "isCritical": False,
    "unsigned_extra": "must not be signed",
}


def _pem(key: ec.EllipticCurvePrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


@pytest.fixture
def key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def test_canonical_bytes_are_sorted_compact_and_unescaped() -> None:
    payload = uf.validate_manifest(MANIFEST)
    assert "unsigned_extra" not in payload
    assert uf.canonical_bytes(payload).decode() == (
        '{"downloadURL":"https://apgiframework.com/downloads/APGI-Appendix-2.1.0.zip",'
        '"isCritical":false,"latestVersion":"2.1.0","minimumVersion":"2.0.0",'
        '"releaseDate":"2026-11-01","releaseNotes":"Fixes — résumé"}'
    )


def test_signature_is_raw_64_byte_and_verifies(key: ec.EllipticCurvePrivateKey) -> None:
    payload = uf.validate_manifest(MANIFEST)
    sig = uf.sign_payload(payload, key)
    assert len(base64.b64decode(sig)) == 64
    assert uf.verify_signature(payload, sig, key.public_key())


def test_tampering_and_wrong_key_are_rejected(key: ec.EllipticCurvePrivateKey) -> None:
    payload = uf.validate_manifest(MANIFEST)
    sig = uf.sign_payload(payload, key)
    assert not uf.verify_signature({**payload, "latestVersion": "9.9.9"}, sig, key.public_key())
    other = ec.generate_private_key(ec.SECP256R1())
    assert not uf.verify_signature(payload, sig, other.public_key())


def test_manifest_validation() -> None:
    with pytest.raises(uf.UpdateFeedError):
        uf.validate_manifest({"latestVersion": "1"})
    with pytest.raises(uf.UpdateFeedError):
        uf.validate_manifest({"latestVersion": "1", "releaseNotes": "n", "isCritical": True})
    with pytest.raises(uf.UpdateFeedError):
        uf.validate_manifest({"latestVersion": "1", "releaseNotes": "n", "isCritical": "yes"})


def test_non_p256_key_rejected() -> None:
    with pytest.raises(uf.UpdateFeedError):
        uf.load_private_key(_pem(ec.generate_private_key(ec.SECP384R1())))


def test_endpoint_fails_closed_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "update_signing_private_key", None)
    r = TestClient(create_app(test_mode=False)).get("/updates.json")
    assert r.status_code == 503
    assert "signature" not in r.text


def test_endpoint_serves_verifiable_feed_anonymously(
    key: ec.EllipticCurvePrivateKey,
    tmp_path: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "manifest.json"  # type: ignore[operator]
    path.write_text(json.dumps(MANIFEST), encoding="utf-8")
    monkeypatch.setattr(settings, "update_manifest_path", str(path))
    monkeypatch.setattr(settings, "update_signing_private_key", _pem(key))
    monkeypatch.setattr(settings, "update_signing_key_id", "appendix-2026-1")
    r = TestClient(create_app(test_mode=False)).get("/updates.json")
    assert r.status_code == 200
    feed = r.json()
    assert feed["algorithm"] == "P256-ECDSA" and feed["keyId"] == "appendix-2026-1"
    signed = {k: v for k, v in feed.items() if k not in ("signature", "keyId", "algorithm")}
    assert uf.verify_signature(signed, feed["signature"], key.public_key())

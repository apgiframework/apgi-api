"""Acquisition upload routes end to end with a fake bucket and an inline verifier."""

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Generator, List, Optional

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from app.database.connection import get_db
from app.database.models import Base
from app.main import create_app
from app.models.schemas import TokenPayload
from app.routes.acquisition import get_verification_enqueuer
from app.services import acquisition_ingest as ingest
from app.services.acquisition_ingest import crc32c
from app.services.acquisition_storage import AcquisitionObjectStore, get_acquisition_store
from app.services.authorization import get_current_user


class FakeBucket(AcquisitionObjectStore):
    def __init__(self) -> None:
        self.objects: Dict[str, bytes] = {}

    def signed_upload_url(self, key: str, *, size_bytes: int, expires_in_s: int) -> str:
        return f"https://bucket.invalid/{key}?ttl={expires_in_s}"

    def size(self, key: str) -> Optional[int]:
        return len(self.objects[key]) if key in self.objects else None

    def read(self, key: str) -> bytes:
        return self.objects[key]


def _user(user_id: str, roles: List[str]) -> TokenPayload:
    return TokenPayload(
        user_id=user_id,
        username=f"{user_id}@example.com",
        roles=roles,
        exp=datetime.now(timezone.utc) + timedelta(hours=1),
    )


class Harness:
    def __init__(self) -> None:
        self.engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.bucket = FakeBucket()
        self.user = _user("owner", ["researcher"])
        self.queued: List[str] = []
        self.app = create_app(test_mode=True)
        self.app.dependency_overrides[get_db] = self._db
        self.app.dependency_overrides[get_current_user] = lambda: self.user
        self.app.dependency_overrides[get_acquisition_store] = lambda: self.bucket
        self.app.dependency_overrides[get_verification_enqueuer] = lambda: self.queued.append
        self.client = TestClient(self.app)

    def _db(self) -> Generator[Session, None, None]:
        db = self.session_factory()
        try:
            yield db
        finally:
            db.close()

    def run_jobs(self) -> None:
        with self.session_factory() as db:
            while self.queued:
                ingest.verify(db, self.bucket, self.queued.pop(0))

    def upload(
        self, name: str, data: bytes, *, put: Optional[bytes] = None, **extra: Any
    ) -> Dict[str, Any]:
        body = {
            "session_id": "s1",
            "object_name": name,
            "size_bytes": len(data),
            "crc32c": crc32c(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            **extra,
        }
        init = self.client.post("/v1/acquisition/uploads", json=body)
        assert init.status_code == 201, init.text
        info = init.json()
        if info["already_accepted"]:
            return {"status": "accepted", "upload_id": info["upload_id"]}
        if put is not None:
            self.bucket.objects[f"s1/{name}"] = put
        commit = self.client.post(f"/v1/acquisition/uploads/{info['upload_id']}/commit")
        assert commit.status_code == 200, commit.text
        return dict(commit.json())


@pytest.fixture
def h() -> Generator[Harness, None, None]:
    harness = Harness()
    yield harness
    harness.engine.dispose()


def test_crc32c_known_vector() -> None:
    assert crc32c(b"123456789") == 0xE3069283


def test_happy_path_accepts_after_sha_job(h: Harness) -> None:
    data = b"x" * 1000
    res = h.upload("data/segment-000000.f32", data, put=data)
    assert res["status"] == "pending"
    h.run_jobs()
    st = h.client.get(
        "/v1/acquisition/uploads/status", params={"key": "s1/data/segment-000000.f32"}
    )
    assert st.status_code == 200
    assert st.json()["status"] == "accepted"
    assert st.json()["sha256"] == hashlib.sha256(data).hexdigest()


def test_init_returns_signed_url_and_ttl(h: Harness) -> None:
    data = b"abc"
    r = h.client.post(
        "/v1/acquisition/uploads",
        json={
            "session_id": "s1",
            "object_name": "meta.json",
            "size_bytes": 3,
            "crc32c": crc32c(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        },
    )
    assert r.status_code == 201
    assert r.json()["signed_url"].startswith("https://bucket.invalid/s1/meta.json")
    assert r.json()["expires_in_s"] > 0


def test_already_accepted_is_idempotent_and_conflict_on_different_content(h: Harness) -> None:
    data = b"hello"
    h.upload("a.bin", data, put=data)
    h.run_jobs()
    again = h.upload("a.bin", data, put=data)
    assert again["status"] == "accepted"
    r = h.client.post(
        "/v1/acquisition/uploads",
        json={
            "session_id": "s1",
            "object_name": "a.bin",
            "size_bytes": 5,
            "crc32c": 1,
            "sha256": "0" * 64,
        },
    )
    assert r.status_code == 409


def test_missing_object_is_rejected(h: Harness) -> None:
    res = h.upload("a.bin", b"data")  # never PUT
    assert res["status"] == "rejected"
    assert "does not exist" in res["reason"]


def test_wrong_size_is_rejected(h: Harness) -> None:
    res = h.upload("a.bin", b"data", put=b"dat")
    assert res["status"] == "rejected"


def test_crc_mismatch_is_quarantined(h: Harness) -> None:
    res = h.upload("a.bin", b"data", put=b"dota")
    assert res["status"] == "quarantined"
    assert "CRC32C" in res["reason"]


def test_sha_mismatch_is_rejected_by_job(h: Harness) -> None:
    good = b"data"
    body = {
        "session_id": "s1",
        "object_name": "a.bin",
        "size_bytes": 4,
        "crc32c": crc32c(good),
        "sha256": "f" * 64,
    }
    up = h.client.post("/v1/acquisition/uploads", json=body).json()
    h.bucket.objects["s1/a.bin"] = good
    assert h.client.post(f"/v1/acquisition/uploads/{up['upload_id']}/commit").json()["status"] == (
        "pending"
    )
    h.run_jobs()
    st = h.client.get("/v1/acquisition/uploads/status", params={"key": "s1/a.bin"}).json()
    assert st["status"] == "rejected"
    assert st["reason"] == "SHA-256 mismatch"


def test_sequence_gap_quarantines_then_releases(h: Harness) -> None:
    d0, d1 = b"zero", b"one!"
    r1 = h.upload("data/segment-000001.f32", d1, put=d1)
    assert r1["status"] == "quarantined" and "sequence gap" in r1["reason"]
    h.upload("data/segment-000000.f32", d0, put=d0)
    h.run_jobs()
    for name in ("data/segment-000000.f32", "data/segment-000001.f32"):
        st = h.client.get("/v1/acquisition/uploads/status", params={"key": f"s1/{name}"})
        assert st.json()["status"] == "accepted", name


def test_commit_is_idempotent(h: Harness) -> None:
    data = b"abc"
    res = h.upload("a.bin", data, put=data)
    again = h.client.post(f"/v1/acquisition/uploads/{res['upload_id']}/commit")
    assert again.status_code == 200
    assert again.json()["status"] == "pending"


def test_other_researcher_forbidden_admin_allowed(h: Harness) -> None:
    data = b"abc"
    h.upload("a.bin", data, put=data)
    h.user = _user("intruder", ["researcher"])
    body = {
        "session_id": "s1",
        "object_name": "b.bin",
        "size_bytes": 3,
        "crc32c": crc32c(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    assert h.client.post("/v1/acquisition/uploads", json=body).status_code == 403
    assert (
        h.client.get("/v1/acquisition/uploads/status", params={"key": "s1/a.bin"}).status_code
        == 403
    )
    h.user = _user("root", ["admin"])
    assert h.client.post("/v1/acquisition/uploads", json=body).status_code == 201


def test_viewer_role_is_forbidden(h: Harness) -> None:
    h.user = _user("v", ["viewer"])
    r = h.client.post(
        "/v1/acquisition/uploads",
        json={
            "session_id": "s1",
            "object_name": "a",
            "size_bytes": 1,
            "crc32c": 0,
            "sha256": "0" * 64,
        },
    )
    assert r.status_code == 403


def test_unknown_key_is_404_and_unknown_upload_is_404(h: Harness) -> None:
    assert (
        h.client.get("/v1/acquisition/uploads/status", params={"key": "s1/none"}).status_code == 404
    )
    assert h.client.post("/v1/acquisition/uploads/nope/commit").status_code == 404


def test_manifest_validation(h: Harness) -> None:
    r = h.client.post(
        "/v1/acquisition/uploads",
        json={
            "session_id": "../x",
            "object_name": "a",
            "size_bytes": 1,
            "crc32c": 0,
            "sha256": "zz",
            "extra": 1,
        },
    )
    assert r.status_code == 422


def test_storage_unconfigured_is_503(h: Harness) -> None:
    h.app.dependency_overrides[get_acquisition_store] = lambda: None
    r = h.client.post(
        "/v1/acquisition/uploads",
        json={
            "session_id": "s1",
            "object_name": "a",
            "size_bytes": 1,
            "crc32c": 0,
            "sha256": "0" * 64,
        },
    )
    assert r.status_code == 503

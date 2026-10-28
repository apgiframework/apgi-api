from fastapi.testclient import TestClient

from app import __version__
from app.main import create_app


def test_release_headers_and_meta():
    client = TestClient(create_app(test_mode=True))
    resp = client.get("/v1/meta")
    assert resp.status_code == 200
    assert resp.headers["X-APGI-Platform-Release"] == __version__
    assert resp.headers["X-APGI-Release-State"] == resp.json()["release_state"]["current"]
    body = resp.json()
    assert body["ethics_number"] is None
    assert body["version_lattice"]["api_contract"] == "v1"

"""TrustedHost allowlist — invalid Host soft-refused."""

from fastapi.testclient import TestClient

from radar.api import app as app_mod


def test_invalid_host_refused():
    client = TestClient(app_mod.app)
    res = client.get("/api/live", headers={"Host": "evil.example"})
    assert res.status_code == 400


def test_localhost_host_ok():
    client = TestClient(app_mod.app, base_url="http://127.0.0.1")
    res = client.get("/api/live")
    assert res.status_code == 200
    assert res.json()["live"] is True

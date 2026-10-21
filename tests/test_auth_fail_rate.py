"""API-key auth-fail soft rate limit."""

from fastapi.testclient import TestClient

from radar.api import app as app_mod
from radar.rate_limit import RateLimiter


def test_auth_fail_rate_soft_refuse(monkeypatch):
    monkeypatch.setenv("RADAR_API_KEY", "lab-key-zzzzzzzz")
    monkeypatch.delenv("RADAR_REQUIRE_API_KEY", raising=False)
    monkeypatch.setattr(
        app_mod,
        "_AUTH_FAIL_LIMITER",
        RateLimiter(max_per_window=2, window_ms=60_000),
    )
    client = TestClient(app_mod.app)
    headers = {"X-Radar-Api-Key": "wrong-key-xxxxxxxx"}
    r1 = client.post("/api/trade/auto", json={"enabled": False}, headers=headers)
    assert r1.status_code == 401
    r2 = client.post("/api/trade/auto", json={"enabled": False}, headers=headers)
    assert r2.status_code == 401
    r3 = client.post("/api/trade/auto", json={"enabled": False}, headers=headers)
    assert r3.status_code == 429
    assert "auth fail" in str(r3.json().get("detail") or "").lower()
    assert "Retry-After" in r3.headers

""" /api/metrics soft rate-limit. """

from fastapi.testclient import TestClient

from radar.api import app as app_mod
from radar.rate_limit import RateLimiter


def test_metrics_rate_soft_refuse(monkeypatch):
    monkeypatch.setattr(
        app_mod,
        "_METRICS_LIMITER",
        RateLimiter(max_per_window=1, window_ms=60_000),
    )
    client = TestClient(app_mod.app)
    r1 = client.get("/api/metrics")
    assert r1.status_code == 200
    r2 = client.get("/api/metrics")
    assert r2.status_code == 429
    assert "metrics" in str(r2.json().get("detail") or "").lower()
    assert "Retry-After" in r2.headers

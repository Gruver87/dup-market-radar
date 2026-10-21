""" /api/live|/api/ready|/api/health soft rate-limit. """

from fastapi.testclient import TestClient

from radar.api import app as app_mod
from radar.rate_limit import RateLimiter


def test_probe_rate_soft_refuse(monkeypatch):
    monkeypatch.setattr(
        app_mod,
        "_PROBE_LIMITER",
        RateLimiter(max_per_window=1, window_ms=60_000),
    )
    client = TestClient(app_mod.app)
    r1 = client.get("/api/live")
    assert r1.status_code == 200
    r2 = client.get("/api/live")
    assert r2.status_code == 429
    assert "probe" in str(r2.json().get("detail") or "").lower()
    assert "Retry-After" in r2.headers


def test_openapi_disabled_by_default():
    client = TestClient(app_mod.app)
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/redoc").status_code == 404


def test_hsts_when_env_set(monkeypatch):
    monkeypatch.setenv("RADAR_HSTS_MAX_AGE", "31536000")
    client = TestClient(app_mod.app)
    res = client.get("/api/live")
    assert res.status_code == 200
    assert res.headers.get("Strict-Transport-Security") == "max-age=31536000"


def test_normalize_lang_oversize():
    from radar.i18n import DEFAULT_LANG, normalize_lang

    assert normalize_lang("x" * 33) == DEFAULT_LANG
    assert normalize_lang("en") == "en"

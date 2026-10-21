"""Browser security headers on API responses."""

from fastapi.testclient import TestClient

from radar.api import app as app_mod


def test_live_sets_security_headers():
    client = TestClient(app_mod.app)
    res = client.get("/api/live")
    assert res.status_code == 200
    assert res.headers.get("X-Content-Type-Options") == "nosniff"
    assert res.headers.get("X-Frame-Options") == "DENY"
    assert res.headers.get("Referrer-Policy") == "no-referrer"
    csp = res.headers.get("Content-Security-Policy") or ""
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert res.headers.get("Cache-Control") == "no-store"
    assert res.headers.get("Cross-Origin-Resource-Policy") == "same-origin"
    assert res.headers.get("X-Permitted-Cross-Domain-Policies") == "none"


def test_dashboard_index_no_store():
    client = TestClient(app_mod.app)
    res = client.get("/")
    assert res.status_code == 200
    assert res.headers.get("Cache-Control") == "no-store"


def test_trace_method_refused():
    client = TestClient(app_mod.app)
    res = client.request("TRACE", "/api/live")
    assert res.status_code == 405
    assert "refused" in str(res.json().get("detail") or "").lower()


def test_health_no_store():
    client = TestClient(app_mod.app)
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.headers.get("Cache-Control") == "no-store"


def test_ops_worldtime_no_store():
    client = TestClient(app_mod.app)
    res = client.get("/api/ops/worldtime")
    assert res.status_code == 200
    assert res.headers.get("Cache-Control") == "no-store"


def test_http_exception_sets_request_id(monkeypatch):
    monkeypatch.setenv("RADAR_API_KEY", "lab-key-zzzzzzzz")
    monkeypatch.delenv("RADAR_REQUIRE_API_KEY", raising=False)
    client = TestClient(app_mod.app)
    res = client.post("/api/trade/auto", json={"enabled": False})
    assert res.status_code == 401
    assert res.headers.get("X-Request-Id")

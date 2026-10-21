"""X-Request-Id sanitize + unhandled 500 redact."""

from fastapi.testclient import TestClient

from radar.api import app as app_mod
from radar.middleware import sanitize_request_id


def test_sanitize_request_id_accepts_safe():
    assert sanitize_request_id("abcDEF12-._x") == "abcDEF12-._x"
    bad = sanitize_request_id("evil\ninject")
    assert "\n" not in bad
    assert len(bad) == 16
    assert sanitize_request_id("x" * 100) != "x" * 100
    assert len(sanitize_request_id("short")) == 16  # <8 → mint


def test_client_request_id_echo_when_safe():
    client = TestClient(app_mod.app)
    res = client.get("/api/live", headers={"X-Request-Id": "lab-req-id-001"})
    assert res.status_code == 200
    assert res.headers.get("X-Request-Id") == "lab-req-id-001"


def test_client_request_id_replaced_when_unsafe():
    client = TestClient(app_mod.app)
    res = client.get("/api/live", headers={"X-Request-Id": "bad id with spaces"})
    assert res.status_code == 200
    rid = res.headers.get("X-Request-Id") or ""
    assert " " not in rid
    assert len(rid) == 16


def test_unhandled_500_no_leak():
    import asyncio

    from starlette.requests import Request

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/live",
        "raw_path": b"/api/live",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 123),
        "server": ("test", 80),
    }
    req = Request(scope)
    req.state.request_id = "lab-500-test-01"

    async def _run():
        return await app_mod._unhandled_exception_handler(
            req, RuntimeError("secret=sk-live-SHOULD-NOT-LEAK")
        )

    resp = asyncio.run(_run())
    assert resp.status_code == 500
    body = resp.body.decode()
    assert "sk-live" not in body
    assert "internal error" in body
    assert "lab-500-test-01" in body


def test_inm_oversize_skipped(monkeypatch):
    fake = {
        "ok": True,
        "schema_version": 2,
        "generated_at_ms": 1_700_000_000_000,
        "score_backend": "python",
        "universe_size": 0,
        "sectors": {},
        "auto_trade": {},
        "cache": {"stats": {}},
    }
    monkeypatch.setattr(app_mod, "_get_snapshot", lambda force=False: dict(fake))
    client = TestClient(app_mod.app)
    r1 = client.get("/api/snapshot")
    assert r1.status_code == 200
    etag = r1.headers.get("ETag")
    # Oversized INM must not 304 even if it starts with the etag
    huge = (etag or "W/\"x\"") + ("A" * 200)
    r2 = client.get("/api/snapshot", headers={"If-None-Match": huge})
    assert r2.status_code == 200

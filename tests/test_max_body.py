"""Max request body Content-Length cap."""

import anyio
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from radar.api import app as app_mod
from radar.middleware import DEFAULT_MAX_BODY_BYTES, MaxBodySizeMiddleware


def test_oversized_body_413():
    client = TestClient(app_mod.app)
    # Real oversized payload so Content-Length matches body size.
    big = b'{"enabled":false,"pad":"' + (b"x" * (DEFAULT_MAX_BODY_BYTES + 1024)) + b'"}'
    res = client.post(
        "/api/trade/auto",
        content=big,
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code == 413
    assert "too large" in res.json()["detail"].lower()


def test_normal_trade_body_not_blocked_by_size(monkeypatch):
    monkeypatch.delenv("RADAR_REQUIRE_API_KEY", raising=False)
    monkeypatch.delenv("RADAR_API_KEY", raising=False)
    client = TestClient(app_mod.app)
    res = client.post("/api/trade/auto", json={"enabled": False})
    # Auth gate off → 200 or business response, never 413
    assert res.status_code != 413


def test_trade_post_missing_content_length_411():
    """Trade JSON POSTs without Content-Length → 411 (no chunked size bypass)."""

    async def ok(_request):
        return PlainTextResponse("ok")

    inner = Starlette(routes=[Route("/api/trade/auto", ok, methods=["POST"])])
    app = MaxBodySizeMiddleware(inner, max_bytes=65_536)

    async def receive():
        return {
            "type": "http.request",
            "body": b'{"enabled":false}',
            "more_body": False,
        }

    messages: list = []

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/trade/auto",
        "raw_path": b"/api/trade/auto",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }

    async def _run():
        await app(scope, receive, send)

    anyio.run(_run)
    start = next(m for m in messages if m["type"] == "http.response.start")
    assert start["status"] == 411

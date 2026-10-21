"""Max URL length + events tail + prefs prune."""

from fastapi.testclient import TestClient

from radar.api import app as app_mod
from radar.middleware import (
    MaxHeadersMiddleware,
    MaxUrlLengthMiddleware,
    sanitize_access_path,
)
from radar.prefs import PrefsStore
from radar.trade import events as ev


def test_sanitize_access_path():
    assert "\n" not in sanitize_access_path("/ok\ninject")
    assert len(sanitize_access_path("x" * 500)) == 256


def test_url_too_long_414(monkeypatch):
    monkeypatch.setattr(
        app_mod,
        "app",
        app_mod.app,  # ensure import side effects done
    )
    # Install a tight middleware for this test via a dedicated app stack is hard;
    # exercise MaxUrlLengthMiddleware directly with a fake request scope.
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient as SC

    async def ok(_request):
        return PlainTextResponse("ok")

    inner = Starlette(routes=[Route("/api/live", ok)])
    inner.add_middleware(MaxUrlLengthMiddleware, max_bytes=80)
    client = SC(inner)
    # path alone may be short; append huge query (must exceed max_bytes)
    res = client.get("/api/live?" + ("a" * 200))
    assert res.status_code == 414
    assert "too long" in str(res.json().get("detail") or "").lower()
    res_ok = client.get("/api/live")
    assert res_ok.status_code == 200


def test_headers_too_many_431():
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient as SC

    async def ok(_request):
        return PlainTextResponse("ok")

    inner = Starlette(routes=[Route("/api/live", ok)])
    inner.add_middleware(MaxHeadersMiddleware, max_count=8, max_bytes=32_768)
    client = SC(inner)
    # httpx/TestClient always sends a few headers; pad beyond max_count
    extra = {f"X-Pad-{i}": "v" for i in range(20)}
    res = client.get("/api/live", headers=extra)
    assert res.status_code == 431


def test_events_tail_read(tmp_path, monkeypatch):
    path = tmp_path / "trade_events.jsonl"
    # Build a file larger than tail window with a marker line at the end
    pad = ("{" + '"x":"' + ("z" * 200) + '"}\n') * 12_000  # ~2.4MB+
    path.write_text(pad + '{"ok":true,"marker":"tail"}\n', encoding="utf-8")
    monkeypatch.setattr(ev, "EVENTS_PATH", path)
    monkeypatch.setattr(ev, "_EVENTS_TAIL_MAX_BYTES", 50_000)
    rows = ev.read_trade_events(limit=5)
    assert rows
    assert rows[-1].get("marker") == "tail"


def test_prefs_prune(tmp_path):
    store = PrefsStore(tmp_path / "prefs.json", max_chats=3)
    store.set_lang(1, "en")
    store.set_lang(2, "ru")
    store.set_lang(3, "en")
    store.set_lang(4, "ru")  # should prune oldest (1)
    assert store.status()["chats"] == 3
    assert store.lang_for(1) == "ru"  # default — pruned
    assert store.lang_for(4) == "ru"


def test_live_still_ok():
    client = TestClient(app_mod.app)
    res = client.get("/api/live")
    assert res.status_code == 200
    # Middleware deletes Server if present
    assert res.headers.get("Server") is None

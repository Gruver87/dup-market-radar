"""Watch prune / ensure + trade events API."""

import time

from fastapi.testclient import TestClient

from radar.api import app as app_mod
from radar.telegram.watches import WatchBook
from radar.trade.events import read_trade_events, record_trade_event


def test_prune_stale_watches(tmp_path):
    book = WatchBook(tmp_path / "w.json")
    book.add(1, "OLDUSDT", market="spot", entry_price_i=1)
    book.add(1, "NEWUSDT", market="spot", entry_price_i=2)
    # age OLD
    with book._lock:
        for k, w in book._watches.items():
            if w["symbol"] == "OLDUSDT":
                w["opened_at_ms"] = int(time.time() * 1000) - 10_000_000
        book._save_unlocked()
    removed = book.prune_stale(ttl_sec=3600)
    assert removed == 1
    syms = {w["symbol"] for w in book.list_for_chat(1)}
    assert syms == {"NEWUSDT"}


def test_ensure_idempotent(tmp_path):
    book = WatchBook(tmp_path / "w2.json")
    assert book.ensure(1, "AAAUSDT", market="spot") is True
    assert book.ensure(1, "AAAUSDT", market="spot") is False
    assert len(book.list_for_chat(1)) == 1


def test_read_trade_events(tmp_path, monkeypatch):
    import radar.trade.events as ev

    path = tmp_path / "trade_events.jsonl"
    monkeypatch.setattr(ev, "EVENTS_PATH", path)
    record_trade_event({"source": "t", "side": "buy", "symbol": "A", "ok": True})
    record_trade_event({"source": "t", "side": "sell", "symbol": "B", "ok": False})
    rows = read_trade_events(limit=10)
    assert len(rows) == 2
    assert rows[-1]["symbol"] == "B"


def test_trade_events_endpoint(tmp_path, monkeypatch):
    import radar.trade.events as ev

    monkeypatch.delenv("RADAR_REQUIRE_API_KEY", raising=False)
    monkeypatch.delenv("RADAR_API_KEY", raising=False)
    path = tmp_path / "trade_events.jsonl"
    monkeypatch.setattr(ev, "EVENTS_PATH", path)
    record_trade_event({"source": "manual", "side": "buy", "symbol": "SOLUSDT", "ok": True})
    client = TestClient(app_mod.app)
    res = client.get("/api/trade/events?limit=5")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["count"] >= 1
    assert any(e.get("symbol") == "SOLUSDT" for e in body["events"])


def test_trade_events_requires_api_key(monkeypatch):
    monkeypatch.setenv("RADAR_API_KEY", "events-secret-key-xyz")
    monkeypatch.delenv("RADAR_REQUIRE_API_KEY", raising=False)
    client = TestClient(app_mod.app)
    deny = client.get("/api/trade/events")
    assert deny.status_code == 401
    ok = client.get(
        "/api/trade/events",
        headers={"X-Radar-Api-Key": "events-secret-key-xyz"},
    )
    assert ok.status_code == 200
    assert ok.json()["ok"] is True

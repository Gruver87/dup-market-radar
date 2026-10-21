"""Manual trade API attaches fill reconcile on live buy."""

from fastapi.testclient import TestClient

from radar.api import app as app_mod
from radar.trade.kucoin_manual import FillInfo, TradeResult


def test_preview_order_has_no_fill():
    client = TestClient(app_mod.app)
    res = client.post(
        "/api/trade/order",
        json={
            "symbol": "BTCUSDT",
            "side": "buy",
            "confirm": False,
            "quote_usdt_micros": 10_000_000,
            "reconcile": True,
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["dry_run"] is True
    assert "fill" not in body


def test_live_buy_includes_fill(monkeypatch):
    def fake_place(req):
        return TradeResult(
            True,
            False,
            "order accepted",
            {"body": {"side": "buy"}},
            {"code": "200000", "data": {"orderId": "manual-ord-9"}},
        )

    def fake_reconcile(order_id, **kwargs):
        assert order_id == "manual-ord-9"
        return FillInfo(
            ok=True,
            order_id=order_id,
            base_size_i=99_000_000,
            deal_funds_micros=10_000_000,
            size_basis="kucoin_fill",
            message="reconciled",
        )

    monkeypatch.setattr(app_mod._trader, "place", fake_place)
    monkeypatch.setattr(app_mod._trader, "reconcile_fill", fake_reconcile)

    client = TestClient(app_mod.app)
    res = client.post(
        "/api/trade/order",
        json={
            "symbol": "SOLUSDT",
            "side": "buy",
            "confirm": True,
            "quote_usdt_micros": 10_000_000,
            "reconcile": True,
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["order_id"] == "manual-ord-9"
    assert body["fill"]["ok"] is True
    assert body["fill"]["base_size_i"] == 99_000_000
    assert body["fill"]["size_basis"] == "kucoin_fill"


def test_reconcile_false_skips_fill(monkeypatch):
    monkeypatch.setattr(
        app_mod._trader,
        "place",
        lambda req: TradeResult(
            True,
            False,
            "order accepted",
            {},
            {"code": "200000", "data": {"orderId": "skip-1"}},
        ),
    )
    called = {"n": 0}

    def boom(*a, **k):
        called["n"] += 1
        raise AssertionError("should not reconcile")

    monkeypatch.setattr(app_mod._trader, "reconcile_fill", boom)

    client = TestClient(app_mod.app)
    res = client.post(
        "/api/trade/order",
        json={
            "symbol": "ETHUSDT",
            "side": "buy",
            "confirm": True,
            "quote_usdt_micros": 5_000_000,
            "reconcile": False,
        },
    )
    assert res.status_code == 200
    assert res.json()["order_id"] == "skip-1"
    assert "fill" not in res.json()
    assert called["n"] == 0

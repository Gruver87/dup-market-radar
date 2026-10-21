from fastapi.testclient import TestClient


def test_trade_order_refuses_usdtm(monkeypatch):
    import radar.api.app as app_mod

    monkeypatch.setattr(app_mod, "api_key_gate_ok", lambda: True)
    monkeypatch.setattr(app_mod, "api_key_required", lambda: False)
    monkeypatch.setattr(app_mod, "_check_trade_rate", lambda req: None)
    client = TestClient(app_mod.app)
    r = client.post(
        "/api/trade/order",
        json={"symbol": "XBTUSDTM", "side": "buy", "confirm": False, "quote_usdt_micros": 10_000_000},
    )
    assert r.status_code == 400
    assert "futures" in str(r.json().get("detail") or "").lower()

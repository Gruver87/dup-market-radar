from fastapi.testclient import TestClient


def test_coerce_and_lookup_endpoint(monkeypatch):
    from radar.api import app as app_mod

    monkeypatch.setenv("RADAR_REQUIRE_API_KEY", "0")
    monkeypatch.setattr(app_mod, "_check_api_key", lambda request: None)
    monkeypatch.setattr(app_mod, "_check_trade_rate", lambda request: None)

    class _T:
        ready = True

        def get_spot_mark_price_i(self, sym: str):
            assert "DOT" in sym.upper()
            return 7_0000_0000

        def get_available_base_i(self, ccy: str):
            assert ccy == "DOT"
            return 50_000_000  # 0.5 DOT

    monkeypatch.setattr(app_mod, "_trader", _T())
    monkeypatch.setattr(
        app_mod._auto,
        "status",
        lambda: {"positions": {}},
    )
    monkeypatch.setattr(app_mod._cache, "peek", lambda: {})

    assert app_mod._coerce_spot_symbol("dot") == "DOTUSDT"
    assert app_mod._coerce_spot_symbol("DOT-USDT") == "DOTUSDT"

    client = TestClient(app_mod.app)
    res = client.get("/api/trade/lookup", params={"symbol": "DOT"})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["symbol"] == "DOTUSDT"
    assert body["mark_price_i"] == 7_0000_0000
    assert body["available_base_i"] == 50_000_000
    assert body["found_mark"] is True

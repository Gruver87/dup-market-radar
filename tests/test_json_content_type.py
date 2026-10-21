"""Trade POST Content-Type must be application/json."""

from fastapi.testclient import TestClient

from radar.api import app as app_mod


def test_trade_order_rejects_non_json_content_type(monkeypatch):
    monkeypatch.delenv("RADAR_REQUIRE_API_KEY", raising=False)
    monkeypatch.delenv("RADAR_API_KEY", raising=False)
    client = TestClient(app_mod.app)
    res = client.post(
        "/api/trade/order",
        content=(
            b'{"symbol":"BTCUSDT","side":"buy","confirm":false,'
            b'"quote_usdt_micros":1000000}'
        ),
        headers={"Content-Type": "text/plain"},
    )
    assert res.status_code == 415
    assert "application/json" in res.json()["detail"]

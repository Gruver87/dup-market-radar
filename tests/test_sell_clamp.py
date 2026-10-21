"""Sell clamp to KuCoin trade-account available."""

from radar.trade.kucoin_manual import KuCoinManualTrader, TradeRequest
from radar.trade.oid_ledger import ClientOidLedger


def test_sell_clamps_to_available(tmp_path, monkeypatch):
    led = ClientOidLedger(tmp_path / "oid.json", ttl_sec=3600)
    trader = KuCoinManualTrader(led, sell_clamp=True, enforce_symbol_meta=False)
    monkeypatch.setattr("radar.trade.kucoin_manual.trading_configured", lambda: True)
    monkeypatch.setattr(trader, "get_available_base_i", lambda ccy: 50_000_000)  # 0.5

    posted = {}

    def fake_post(path, body):
        posted.update(body)
        return {"code": "200000", "data": {"orderId": "sell-1"}}

    monkeypatch.setattr(trader, "_signed_post", fake_post)

    r = trader.place(
        TradeRequest(
            symbol="SOLUSDT",
            side="sell",
            confirm=True,
            base_size_i=100_000_000,  # request 1.0
            client_oid="sell-clamp-oid-001",
        )
    )
    assert r.ok is True
    assert "clamped" in r.message.lower()
    assert posted["size"] == "0.5"
    assert r.request.get("clamped_base_size_i") == 50_000_000
    assert r.request.get("requested_base_size_i") == 100_000_000


def test_sell_refuses_zero_balance(tmp_path, monkeypatch):
    led = ClientOidLedger(tmp_path / "oid.json", ttl_sec=3600)
    trader = KuCoinManualTrader(led, sell_clamp=True, enforce_symbol_meta=False)
    monkeypatch.setattr("radar.trade.kucoin_manual.trading_configured", lambda: True)
    monkeypatch.setattr(trader, "get_available_base_i", lambda ccy: 0)

    called = {"n": 0}

    def fake_post(path, body):
        called["n"] += 1
        return {"code": "200000", "data": {"orderId": "x"}}

    monkeypatch.setattr(trader, "_signed_post", fake_post)

    r = trader.place(
        TradeRequest(
            symbol="ETHUSDT",
            side="sell",
            confirm=True,
            base_size_i=1_000_000,
            client_oid="sell-zero-oid-002",
        )
    )
    assert r.ok is False
    assert "available" in r.message.lower()
    assert called["n"] == 0


def test_parse_accounts_available():
    trader = KuCoinManualTrader(sell_clamp=False, enforce_symbol_meta=False)

    def fake_get(path, params=None):
        assert path == "/api/v1/accounts"
        return {
            "code": "200000",
            "data": [
                {"currency": "BTC", "type": "trade", "available": "0.01", "balance": "0.01"},
            ],
        }

    trader.refresh_creds = lambda: None  # type: ignore[method-assign]
    import radar.trade.kucoin_manual as km

    # bypass keys check
    orig = km.trading_configured
    km.trading_configured = lambda: True
    try:
        trader._signed_get = fake_get  # type: ignore[method-assign]
        assert trader.get_available_base_i("BTC") == 1_000_000  # 0.01 * 1e8
    finally:
        km.trading_configured = orig

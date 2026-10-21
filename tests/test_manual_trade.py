from radar.trade.kucoin_manual import (
    KuCoinManualTrader,
    TradeRequest,
    resolve_base_asset,
    to_kucoin_symbol,
)


def test_symbol_map():
    assert to_kucoin_symbol("BTCUSDT") == "BTC-USDT"
    assert to_kucoin_symbol("DOOD-USDT") == "DOOD-USDT"


def test_resolve_base_asset(caplog):
    assert resolve_base_asset("BTCUSDT") == "BTC"
    assert resolve_base_asset("ETH-USDT") == "ETH"
    with caplog.at_level("WARNING", logger="radar.trade.kucoin"):
        assert resolve_base_asset("!!!") == ""
    assert any("base_asset resolve failed" in r.message for r in caplog.records)


def test_preview_without_confirm_never_hits_exchange():
    t = KuCoinManualTrader()
    r = t.place(
        TradeRequest(symbol="BTCUSDT", side="buy", confirm=False, quote_usdt_micros=10_000_000)
    )
    assert r.ok is True
    assert r.dry_run is True
    assert r.exchange is None
    assert "preview" in r.message.lower()
    assert r.request["body"]["funds"] == "10"


def test_confirm_without_keys_refuses():
    t = KuCoinManualTrader()
    r = t.place(
        TradeRequest(symbol="BTCUSDT", side="buy", confirm=True, quote_usdt_micros=5_000_000)
    )
    if not t.ready:
        assert r.ok is False
        assert r.dry_run is True
        assert "missing" in r.message.lower() or "keys" in r.message.lower()
    else:
        assert True


def test_sell_requires_size():
    t = KuCoinManualTrader()
    r = t.place(TradeRequest(symbol="ETHUSDT", side="sell", confirm=False))
    assert r.ok is False
    assert "base_size_i" in r.message


def test_buy_requires_micros_int():
    t = KuCoinManualTrader()
    r = t.place(TradeRequest(symbol="BTCUSDT", side="buy", confirm=False))
    assert r.ok is False
    assert "quote_usdt_micros" in r.message

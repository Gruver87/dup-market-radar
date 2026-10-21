"""KuCoin symbol meta minFunds / baseMinSize gates."""

import time

from radar.trade.kucoin_manual import KuCoinManualTrader, TradeRequest
from radar.trade.oid_ledger import ClientOidLedger
from radar.trade.symbol_meta import SymbolMeta, SymbolMetaCache, parse_symbol_row


def test_symbol_meta_bad_base_refuses_refresh():
    cache = SymbolMetaCache(base_url="https://evil.example")
    st = cache.status()
    assert st["base_url_ok"] is False
    assert cache.refresh() == 0


def test_parse_symbol_row():
    meta = parse_symbol_row(
        {
            "symbol": "BTC-USDT",
            "enableTrading": True,
            "baseMinSize": "0.00001",
            "minFunds": "1",
            "quoteMinSize": "2",
            "quoteIncrement": "0.000001",
            "baseIncrement": "0.00001",
        }
    )
    assert meta is not None
    assert meta.pair == "BTC-USDT"
    assert meta.min_funds_micros == 1_000_000
    assert meta.quote_min_micros == 2_000_000
    assert meta.quote_inc_micros == 1  # 0.000001 USDT * 1e6
    assert meta.effective_buy_min_micros() == 2_000_000  # max(minFunds, quoteMin)
    assert meta.base_min_i == 1_000  # 0.00001 * 1e8
    assert meta.validate_buy(1_500_000) is not None  # under quoteMin
    assert meta.validate_buy(2_000_000) is None
    assert meta.validate_sell(100) is not None
    assert meta.validate_sell(1_000) is None
    assert meta.floor_size(2_500) == 2_000  # inc=1000
    assert meta.floor_funds(1_000_007) == 1_000_007  # inc=1 в†’ no change
    meta_coarse = parse_symbol_row(
        {
            "symbol": "AAA-USDT",
            "enableTrading": True,
            "baseMinSize": "1",
            "minFunds": "1",
            "quoteMinSize": "1",
            "quoteIncrement": "0.01",
            "baseIncrement": "1",
        }
    )
    assert meta_coarse is not None
    assert meta_coarse.quote_inc_micros == 10_000  # 0.01 USDT
    assert meta_coarse.floor_funds(12_345) == 10_000


def test_place_floors_buy_funds_to_quote_increment(tmp_path, monkeypatch):
    cache = SymbolMetaCache(ttl_sec=3600)
    cache._by_pair["ETH-USDT"] = SymbolMeta(
        pair="ETH-USDT",
        enable_trading=True,
        base_min_i=1_000,
        min_funds_micros=1_000_000,
        base_inc_i=1_000,
        quote_inc_micros=10_000,  # 0.01 USDT
    )
    cache._fetched_at = int(time.time())
    led = ClientOidLedger(tmp_path / "oid.json", ttl_sec=3600)
    trader = KuCoinManualTrader(
        led, sell_clamp=False, symbol_meta=cache, enforce_symbol_meta=True
    )
    monkeypatch.setattr("radar.trade.kucoin_manual.trading_configured", lambda: True)
    r = trader.place(
        TradeRequest(
            symbol="ETHUSDT",
            side="buy",
            confirm=False,
            quote_usdt_micros=10_015_000,  # 10.015 в†’ floor 10.01
        )
    )
    assert r.ok is True
    assert r.request["body"]["funds"] == "10.01"
    assert "funds floored" in str(r.request.get("funds_floor") or "")

def test_place_refuses_under_min_funds(tmp_path, monkeypatch):
    cache = SymbolMetaCache(ttl_sec=3600)
    cache._by_pair["BTC-USDT"] = SymbolMeta(
        pair="BTC-USDT",
        enable_trading=True,
        base_min_i=1_000,
        min_funds_micros=5_000_000,
        base_inc_i=1_000,
    )
    cache._fetched_at = int(time.time())
    led = ClientOidLedger(tmp_path / "oid.json", ttl_sec=3600)
    trader = KuCoinManualTrader(
        led, sell_clamp=False, symbol_meta=cache, enforce_symbol_meta=True
    )
    monkeypatch.setattr("radar.trade.kucoin_manual.trading_configured", lambda: True)

    r = trader.place(
        TradeRequest(
            symbol="BTCUSDT",
            side="buy",
            confirm=False,
            quote_usdt_micros=1_000_000,  # 1 USDT < 5
        )
    )
    assert r.ok is False
    assert "minNotional" in r.message or "minFunds" in r.message


def test_place_allows_when_above_min(tmp_path, monkeypatch):
    cache = SymbolMetaCache(ttl_sec=3600)
    cache._by_pair["ETH-USDT"] = SymbolMeta(
        pair="ETH-USDT",
        enable_trading=True,
        base_min_i=1_000,
        min_funds_micros=1_000_000,
        base_inc_i=1_000,
    )
    cache._fetched_at = int(time.time())
    led = ClientOidLedger(tmp_path / "oid.json", ttl_sec=3600)
    trader = KuCoinManualTrader(
        led, sell_clamp=False, symbol_meta=cache, enforce_symbol_meta=True
    )
    monkeypatch.setattr("radar.trade.kucoin_manual.trading_configured", lambda: True)
    monkeypatch.setattr(
        trader,
        "_signed_post",
        lambda path, body: {"code": "200000", "data": {"orderId": "m1"}},
    )
    r = trader.place(
        TradeRequest(
            symbol="ETHUSDT",
            side="buy",
            confirm=True,
            quote_usdt_micros=10_000_000,
            client_oid="meta-ok-oid-abcdef",
        )
    )
    assert r.ok is True

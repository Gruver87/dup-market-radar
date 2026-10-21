"""Fat-finger order size caps."""

import time

from radar.trade.kucoin_manual import KuCoinManualTrader, TradeRequest
from radar.trade.oid_ledger import ClientOidLedger
from radar.trade.symbol_meta import SymbolMeta, SymbolMetaCache


def test_buy_over_max_quote_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "radar.trade.kucoin_manual.load_config",
        lambda: {"api": {"max_order_quote_usdt_micros": 5_000_000}},  # 5 USDT
    )
    cache = SymbolMetaCache(ttl_sec=3600)
    cache._by_pair["BTC-USDT"] = SymbolMeta(
        pair="BTC-USDT",
        enable_trading=True,
        base_min_i=1,
        min_funds_micros=1,
        base_inc_i=1,
    )
    cache._fetched_at = int(time.time())
    led = ClientOidLedger(tmp_path / "oid.json", ttl_sec=3600)
    trader = KuCoinManualTrader(
        led, sell_clamp=False, symbol_meta=cache, enforce_symbol_meta=False
    )
    monkeypatch.setattr("radar.trade.kucoin_manual.trading_configured", lambda: True)
    r = trader.place(
        TradeRequest(
            symbol="BTCUSDT",
            side="buy",
            confirm=False,
            quote_usdt_micros=10_000_000,  # 10 USDT > 5
        )
    )
    assert r.ok is False
    assert "max_order_quote_usdt_micros" in r.message


def test_sell_over_max_base_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "radar.trade.kucoin_manual.load_config",
        lambda: {"api": {"max_order_base_size_i": 1_000}},
    )
    led = ClientOidLedger(tmp_path / "oid.json", ttl_sec=3600)
    trader = KuCoinManualTrader(led, sell_clamp=False, enforce_symbol_meta=False)
    monkeypatch.setattr("radar.trade.kucoin_manual.trading_configured", lambda: True)
    r = trader.place(
        TradeRequest(
            symbol="ETHUSDT",
            side="sell",
            confirm=False,
            base_size_i=5_000,
        )
    )
    assert r.ok is False
    assert "max_order_base_size_i" in r.message

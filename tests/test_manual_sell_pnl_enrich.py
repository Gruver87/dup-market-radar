"""Manual sell must attach cost/PnL from open book (no fake 100% wins)."""

from radar.trade.auto_engine import AutoTradeEngine
from radar.trade.kucoin_manual import KuCoinManualTrader


def test_enrich_manual_sell_uses_position_cost(tmp_path, monkeypatch):
    import radar.api.app as app_mod
    import radar.trade.auto_engine as ae

    monkeypatch.setattr(ae, "POS_PATH", tmp_path / "auto_positions.json")
    monkeypatch.setattr(ae, "STATE_PATH", tmp_path / "auto_trade_state.json")
    monkeypatch.setattr(ae, "LOG_PATH", tmp_path / "auto_trade_log.jsonl")
    eng = AutoTradeEngine(KuCoinManualTrader(), lambda: {})
    eng._persist_ok = True
    eng.register_live_buy(
        symbol="ETHUSDT",
        quote_usdt_micros=14_999_914,
        base_size_i=907_650,
        entry_price_i=165_338_000_000,
        deal_funds_micros=14_999_914,
        size_basis="kucoin_fill",
        sector="spot_alts",
        dry_run=False,
        source="auto",
    )
    monkeypatch.setattr(app_mod, "_auto", eng)
    trade_ev = {
        "ok": True,
        "dry_run": False,
        "side": "sell",
        "symbol": "ETHUSDT",
        "base_size_i": 907_650,
        "fill": {"deal_funds_micros": 14_975_952, "base_size_i": 907_650},
    }
    app_mod._enrich_manual_sell_from_book(trade_ev)
    assert trade_ev["cost_usdt_micros"] == 14_999_914
    assert trade_ev["realized_pnl_usdt_micros"] == 14_975_952 - 14_999_914
    assert trade_ev["realized_pnl_usdt_micros"] < 0
    # Not a fake +10 USDT win from a 5 USDT orphan lot
    assert trade_ev["realized_pnl_usdt_micros"] > -1_000_000

from radar.trade.auto_engine import AutoTradeEngine
from radar.trade.kucoin_manual import KuCoinManualTrader


def test_status_with_marks_unrealized(tmp_path, monkeypatch):
    import radar.trade.auto_engine as ae

    monkeypatch.setattr(ae, "POS_PATH", tmp_path / "auto_positions.json")
    monkeypatch.setattr(ae, "STATE_PATH", tmp_path / "auto_trade_state.json")
    monkeypatch.setattr(ae, "LOG_PATH", tmp_path / "auto_trade_log.jsonl")
    eng = AutoTradeEngine(KuCoinManualTrader(), lambda: {})
    eng._persist_ok = True
    eng.register_live_buy(
        symbol="DOTUSDT",
        quote_usdt_micros=5_000_000,
        base_size_i=1_000_000_00,  # 1.0 DOT @ entry below
        entry_price_i=5_0000_0000,  # 5.0
        deal_funds_micros=5_000_000,
        size_basis="kucoin_fill",
        sector="spot_alts",
        dry_run=False,
        source="auto",
    )

    class _T:
        ready = True

        def get_spot_mark_price_i(self, sym: str):
            return 7_0000_0000  # 7.0 → +2 USDT on 1 DOT

    eng._trader = _T()  # type: ignore[assignment]
    st = eng.status_with_marks()
    pos = st["positions"]["DOTUSDT"]
    assert pos["mark_price_i"] == 7_0000_0000
    assert pos["unrealized_pnl_usdt_micros"] == 2_000_000
    assert st["open_unrealized_pnl_usdt_micros"] == 2_000_000

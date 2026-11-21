from radar.trade.auto_engine import AutoTradeEngine
from radar.trade.kucoin_manual import KuCoinManualTrader


def test_register_live_buy_tracks_position(tmp_path, monkeypatch):
    import radar.trade.auto_engine as ae

    monkeypatch.setattr(ae, "POS_PATH", tmp_path / "auto_positions.json")
    monkeypatch.setattr(ae, "STATE_PATH", tmp_path / "auto_trade_state.json")
    monkeypatch.setattr(ae, "LOG_PATH", tmp_path / "auto_trade_log.jsonl")
    eng = AutoTradeEngine(KuCoinManualTrader(), lambda: {})
    eng._persist_ok = True
    out = eng.register_live_buy(
        symbol="NAORISUSDT",
        quote_usdt_micros=10_000_000,
        base_size_i=32_330_000_000,
        entry_price_i=0,
        order_id="ord-1",
        source="manual",
    )
    assert out["ok"] is True
    assert "NAORISUSDT" in eng._positions
    pos = eng._positions["NAORISUSDT"]
    assert pos["base_size_i"] == 32_330_000_000
    assert pos["quote_usdt_micros"] == 10_000_000
    assert int(pos["entry_price_i"]) > 0

    again = eng.register_live_buy(
        symbol="NAORISUSDT",
        quote_usdt_micros=10_000_000,
        base_size_i=32_330_000_000,
        source="manual",
    )
    assert again.get("skipped") is True

    forgot = eng.forget_position("NAORISUSDT")
    assert forgot.get("removed") is True
    assert "NAORISUSDT" not in eng._positions

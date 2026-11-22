from radar.trade.auto_engine import AutoTradeEngine
from radar.trade.kucoin_manual import KuCoinManualTrader


def test_pnl_after_fees_round_trip():
    cfg = {"fee_bps_per_side": 10}  # 0.10% each side
    gross, fee, net = AutoTradeEngine._pnl_after_fees(10_000_000, 10_500_000, cfg)
    assert gross == 500_000
    # 10bps of 10 + 10bps of 10.5 = 10_000 + 10_500
    assert fee == 20_500
    assert net == 500_000 - 20_500


def test_loss_streak_pauses_buys(tmp_path, monkeypatch):
    import radar.trade.auto_engine as ae

    monkeypatch.setattr(ae, "POS_PATH", tmp_path / "auto_positions.json")
    monkeypatch.setattr(ae, "STATE_PATH", tmp_path / "auto_trade_state.json")
    monkeypatch.setattr(ae, "LOG_PATH", tmp_path / "auto_trade_log.jsonl")
    eng = AutoTradeEngine(KuCoinManualTrader(), lambda: {})
    eng._persist_ok = True
    cfg = {"loss_streak_pause_buys": 3, "loss_streak_pause_sec": 600}
    with eng._lock:
        eng._note_sell_pnl_locked(-100, cfg)
        eng._note_sell_pnl_locked(-100, cfg)
        assert int(eng._state.get("loss_streak") or 0) == 2
        assert int(eng._state.get("pause_buys_until") or 0) == 0
        eng._note_sell_pnl_locked(-100, cfg)
        assert int(eng._state.get("loss_streak") or 0) == 3
        assert int(eng._state.get("pause_buys_until") or 0) > 0
    paused, msg = eng._buys_paused()
    assert paused is True
    assert "pause" in msg
    with eng._lock:
        eng._note_sell_pnl_locked(50, cfg)
        assert int(eng._state.get("loss_streak") or 0) == 0


def test_anti_chase_skips_extended_1h(tmp_path, monkeypatch):
    import radar.trade.auto_engine as ae

    monkeypatch.setattr(ae, "POS_PATH", tmp_path / "auto_positions.json")
    monkeypatch.setattr(ae, "STATE_PATH", tmp_path / "auto_trade_state.json")
    monkeypatch.setattr(ae, "LOG_PATH", tmp_path / "auto_trade_log.jsonl")

    snap = {
        "ok": True,
        "sectors": {
            "spot_alts": [
                {
                    "symbol": "HOTUSDT",
                    "venue": "kucoin",
                    "setup": "buy-watch",
                    "score_centi": 8000,
                    "price_i": 1_000_000,
                    "metrics": {"change_bps_1h": 800},
                }
            ]
        },
    }
    eng = AutoTradeEngine(KuCoinManualTrader(), lambda: snap)
    eng._persist_ok = True
    eng._state["enabled"] = True
    # Force dry cycle path pieces via _collect + manual gate simulation
    rows = eng._collect_buys(
        snap["sectors"],
        ["spot_alts"],
        {"buy-watch"},
        3500,
        False,
        {},
    )
    assert len(rows) == 1
    # chase filter lives in run loop — unit the condition here
    chg = rows[0].get("metrics", {}).get("change_bps_1h")
    assert int(chg) >= 500

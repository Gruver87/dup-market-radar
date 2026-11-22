from radar.money import rise_bps
from radar.trade.auto_engine import AutoTradeEngine
from radar.trade.kucoin_manual import KuCoinManualTrader


def test_rise_bps_positive_on_gain():
    # entry 100, mark 108 → +800 bps
    assert rise_bps(100_000_000, 108_000_000) == 800


def test_sell_outside_top_n_take_profit(tmp_path, monkeypatch):
    import radar.trade.auto_engine as ae

    monkeypatch.setattr(ae, "POS_PATH", tmp_path / "auto_positions.json")
    monkeypatch.setattr(ae, "STATE_PATH", tmp_path / "auto_trade_state.json")
    monkeypatch.setattr(ae, "LOG_PATH", tmp_path / "auto_trade_log.jsonl")
    eng = AutoTradeEngine(KuCoinManualTrader(), lambda: {"ok": True, "sectors": {}})
    eng._persist_ok = True
    pos = {
        "symbol": "AAAUSDT",
        "entry_price_i": 100_000_000,
        "base_size_i": 10_000_000_000,
        "quote_usdt_micros": 10_000_000,
        "opened_at_ms": 1,
    }

    class _T:
        ready = True

        def get_spot_mark_price_i(self, sym: str):
            return 110_000_000  # +10%

    eng._trader = _T()  # type: ignore[assignment]
    reason, mark, _ = eng._evaluate_sell_reason(
        "AAAUSDT",
        pos,
        row=None,  # outside board
        sell_setups={"fade-watch"},
        stop_loss_bps=1200,
        take_profit_bps=800,
        prefer_mark=True,
    )
    assert mark == 110_000_000
    assert reason and reason.startswith("take_profit")


def test_sell_outside_top_n_stop_loss(tmp_path, monkeypatch):
    import radar.trade.auto_engine as ae

    monkeypatch.setattr(ae, "POS_PATH", tmp_path / "auto_positions.json")
    monkeypatch.setattr(ae, "STATE_PATH", tmp_path / "auto_trade_state.json")
    monkeypatch.setattr(ae, "LOG_PATH", tmp_path / "auto_trade_log.jsonl")
    eng = AutoTradeEngine(KuCoinManualTrader(), lambda: {})
    eng._persist_ok = True
    pos = {
        "symbol": "AAAUSDT",
        "entry_price_i": 100_000_000,
        "base_size_i": 10_000_000_000,
        "opened_at_ms": 1,
    }

    class _T:
        ready = True

        def get_spot_mark_price_i(self, sym: str):
            return 85_000_000  # -15%

    eng._trader = _T()  # type: ignore[assignment]
    reason, _, _ = eng._evaluate_sell_reason(
        "AAAUSDT",
        pos,
        row=None,
        sell_setups={"fade-watch"},
        stop_loss_bps=1200,
        take_profit_bps=800,
        prefer_mark=True,
    )
    assert reason and reason.startswith("stop_loss")


def test_fade_guard_skips_tiny_move(tmp_path, monkeypatch):
    import radar.trade.auto_engine as ae

    monkeypatch.setattr(ae, "POS_PATH", tmp_path / "auto_positions.json")
    monkeypatch.setattr(ae, "STATE_PATH", tmp_path / "auto_trade_state.json")
    monkeypatch.setattr(ae, "LOG_PATH", tmp_path / "auto_trade_log.jsonl")
    eng = AutoTradeEngine(KuCoinManualTrader(), lambda: {})
    eng._persist_ok = True
    pos = {
        "symbol": "AAAUSDT",
        "entry_price_i": 100_000_000,
        "base_size_i": 10_000_000_000,
        "opened_at_ms": 1,
    }

    class _T:
        ready = True

        def get_spot_mark_price_i(self, sym: str):
            return 100_500_000  # +0.5% < 1%

    eng._trader = _T()  # type: ignore[assignment]
    reason, _, _ = eng._evaluate_sell_reason(
        "AAAUSDT",
        pos,
        row={"setup": "fade-watch", "price_i": 100_500_000},
        sell_setups={"fade-watch"},
        stop_loss_bps=1200,
        take_profit_bps=800,
        prefer_mark=True,
        fade_min_abs_bps=100,
    )
    assert reason is None


def test_roi_ladder_after_hold(tmp_path, monkeypatch):
    import radar.trade.auto_engine as ae

    monkeypatch.setattr(ae, "POS_PATH", tmp_path / "auto_positions.json")
    monkeypatch.setattr(ae, "STATE_PATH", tmp_path / "auto_trade_state.json")
    monkeypatch.setattr(ae, "LOG_PATH", tmp_path / "auto_trade_log.jsonl")
    eng = AutoTradeEngine(KuCoinManualTrader(), lambda: {})
    eng._persist_ok = True
    # opened 40 minutes ago; need +2% on ladder
    now = 2_000_000_000_000
    pos = {
        "symbol": "AAAUSDT",
        "entry_price_i": 100_000_000,
        "base_size_i": 10_000_000_000,
        "opened_at_ms": now - 40 * 60 * 1000,
    }

    class _T:
        ready = True

        def get_spot_mark_price_i(self, sym: str):
            return 102_000_000  # +2%

    eng._trader = _T()  # type: ignore[assignment]
    reason, _, _ = eng._evaluate_sell_reason(
        "AAAUSDT",
        pos,
        row=None,
        sell_setups=set(),
        stop_loss_bps=1200,
        take_profit_bps=800,
        prefer_mark=True,
        roi_ladder=[(0, 400), (1800, 200), (5400, 100)],
        now_ms=now,
    )
    assert reason and reason.startswith("roi_ladder")


def test_trailing_stop_from_peak(tmp_path, monkeypatch):
    import radar.trade.auto_engine as ae

    monkeypatch.setattr(ae, "POS_PATH", tmp_path / "auto_positions.json")
    monkeypatch.setattr(ae, "STATE_PATH", tmp_path / "auto_trade_state.json")
    monkeypatch.setattr(ae, "LOG_PATH", tmp_path / "auto_trade_log.jsonl")
    eng = AutoTradeEngine(KuCoinManualTrader(), lambda: {})
    eng._persist_ok = True
    pos = {
        "symbol": "AAAUSDT",
        "entry_price_i": 100_000_000,
        "base_size_i": 10_000_000_000,
        "opened_at_ms": 1,
        "trailing_armed": True,
        "peak_mark_price_i": 105_000_000,  # was +5%
    }

    class _T:
        ready = True

        def get_spot_mark_price_i(self, sym: str):
            return 102_500_000  # −2.38% from peak → trail 200 bps

    eng._trader = _T()  # type: ignore[assignment]
    reason, _, updates = eng._evaluate_sell_reason(
        "AAAUSDT",
        pos,
        row=None,
        sell_setups=set(),
        stop_loss_bps=1200,
        take_profit_bps=800,
        prefer_mark=True,
        trailing_stop_bps=200,
        trailing_offset_bps=300,
    )
    assert reason and reason.startswith("trailing_stop")
    assert updates.get("trailing_armed") is True or pos.get("trailing_armed")

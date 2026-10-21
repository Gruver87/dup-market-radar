
from radar.trade.auto_engine import AutoTradeEngine, FUTURES_BLOCK
from radar.trade.kucoin_manual import KuCoinManualTrader, TradeResult


def test_futures_blocked_constant():
    assert "futures" in FUTURES_BLOCK
    assert "futures_long" in FUTURES_BLOCK


def test_collect_buys_skips_futures_and_held():
    trader = KuCoinManualTrader()
    eng = AutoTradeEngine(trader, lambda: {})
    sectors = {
        "spot_alts": [
            {
                "symbol": "AAAUSDT",
                "setup": "buy-watch",
                "score_centi": 8000,
                "price_i": 100_000_000,
                "risk_flags": [],
                "venue": "kucoin",
                "market": "spot",
            }
        ],
        "futures": [
            {
                "symbol": "BTCUSDT",
                "setup": "long",
                "score_centi": 9900,
                "price_i": 100_000_000,
                "risk_flags": [],
                "venue": "kucoin",
                "market": "futures",
            }
        ],
    }
    buys = eng._collect_buys(
        sectors,
        ["spot_alts", "futures"],
        {"buy-watch"},
        5000,
        True,
        {},
    )
    assert [b["symbol"] for b in buys] == ["AAAUSDT"]
    assert isinstance(buys[0]["score_centi"], int)


def test_set_enabled_false_always_ok():
    eng = AutoTradeEngine(KuCoinManualTrader(), lambda: {"sectors": {}})
    st = eng.set_enabled(False)
    assert st["enabled"] is False
    assert st["futures_excluded"] is True
    assert st["manual_still_available"] is True
    assert st["persistence_ok"] is True


def test_failed_sell_keeps_position(tmp_path, monkeypatch):
    import radar.trade.auto_engine as ae

    monkeypatch.setattr(ae, "POS_PATH", tmp_path / "auto_positions.json")
    monkeypatch.setattr(ae, "STATE_PATH", tmp_path / "auto_trade_state.json")
    monkeypatch.setattr(ae, "LOG_PATH", tmp_path / "auto_trade_log.jsonl")

    class FailSellTrader(KuCoinManualTrader):
        def place(self, req):  # type: ignore[override]
            return TradeResult(False, True, "exchange refused", {})

    eng = AutoTradeEngine(
        FailSellTrader(),
        lambda: {
            "ok": True,
            "mtf_ensure_ok": True,
            "score_backend": "radar_native",
            "sectors": {
                "spot_alts": [
                    {
                        "symbol": "SOLUSDT",
                        "setup": "fade-watch",
                        "score_centi": 6000,
                        "price_i": 90_000_000_000,
                        "risk_flags": [],
                        "venue": "kucoin",
                        "market": "spot",
                    }
                ]
            }
        },
    )
    eng._persist_ok = True
    eng._positions = {
        "SOLUSDT": {
            "symbol": "SOLUSDT",
            "entry_price_i": 100_000_000_000,
            "base_size_i": 1_000_000,
            "quote_usdt_micros": 10_000_000,
        }
    }
    eng._save_positions_locked()

    # Force dry_run false path via config override isn't easy — patch _exec_sell result
    monkeypatch.setattr(
        eng,
        "_exec_sell",
        lambda *a, **k: {
            "ok": False,
            "dry_run": True,
            "side": "sell",
            "symbol": "SOLUSDT",
            "message": "fail",
        },
    )
    # Need sell reason: fade-watch is sell_setup; also stop loss
    summary = eng.run_once()
    assert any(a.get("ok") is False for a in summary["actions"])
    assert "SOLUSDT" in eng._positions  # kept after failed sell


def test_successful_sell_pops_position(tmp_path, monkeypatch):
    import radar.trade.auto_engine as ae

    monkeypatch.setattr(ae, "POS_PATH", tmp_path / "auto_positions.json")
    monkeypatch.setattr(ae, "STATE_PATH", tmp_path / "auto_trade_state.json")
    monkeypatch.setattr(ae, "LOG_PATH", tmp_path / "auto_trade_log.jsonl")

    eng = AutoTradeEngine(
        KuCoinManualTrader(),
        lambda: {
            "ok": True,
            "mtf_ensure_ok": True,
            "score_backend": "radar_native",
            "sectors": {
                "spot_alts": [
                    {
                        "symbol": "SOLUSDT",
                        "setup": "fade-watch",
                        "score_centi": 6000,
                        "price_i": 90_000_000_000,
                        "risk_flags": [],
                        "venue": "kucoin",
                        "market": "spot",
                    }
                ]
            }
        },
    )
    eng._persist_ok = True
    eng._positions = {
        "SOLUSDT": {
            "symbol": "SOLUSDT",
            "entry_price_i": 100_000_000_000,
            "base_size_i": 1_000_000,
            "quote_usdt_micros": 10_000_000,
        }
    }
    monkeypatch.setattr(
        eng,
        "_exec_sell",
        lambda *a, **k: {
            "ok": True,
            "dry_run": True,
            "side": "sell",
            "symbol": "SOLUSDT",
            "message": "ok",
        },
    )
    eng.run_once()
    assert "SOLUSDT" not in eng._positions
    assert (tmp_path / "auto_positions.json").exists()


def test_corrupt_positions_blocks_enable(tmp_path, monkeypatch):
    import radar.trade.auto_engine as ae

    pos = tmp_path / "auto_positions.json"
    pos.write_text("{not-json", encoding="utf-8")
    monkeypatch.setattr(ae, "POS_PATH", pos)
    monkeypatch.setattr(ae, "STATE_PATH", tmp_path / "auto_trade_state.json")
    monkeypatch.setattr(ae, "LOG_PATH", tmp_path / "auto_trade_log.jsonl")

    eng = AutoTradeEngine(KuCoinManualTrader(), lambda: {"sectors": {}})
    assert eng._persist_ok is False
    try:
        eng.set_enabled(True)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "corrupt" in str(exc).lower() or "persist" in str(exc).lower()

def test_auto_refuses_snapshot_not_ok(tmp_path, monkeypatch):
    import radar.trade.auto_engine as ae

    monkeypatch.setattr(ae, "POS_PATH", tmp_path / "auto_positions.json")
    monkeypatch.setattr(ae, "STATE_PATH", tmp_path / "auto_trade_state.json")
    monkeypatch.setattr(ae, "LOG_PATH", tmp_path / "auto_trade_log.jsonl")

    eng = AutoTradeEngine(
        KuCoinManualTrader(),
        lambda: {
            "ok": False,
            "mtf_ensure_ok": True,
            "score_backend": "radar_native",
            "sectors": {
                "spot_alts": [
                    {
                        "symbol": "SOLUSDT",
                        "setup": "buy-watch",
                        "score_centi": 9000,
                        "price_i": 100_000_000_000,
                        "risk_flags": [],
                        "venue": "kucoin",
                        "market": "spot",
                    }
                ]
            },
        },
    )
    eng._persist_ok = True
    with eng._lock:
        eng._state["enabled"] = True
    summary = eng.run_once()
    assert summary["ok"] is False
    assert "not ok" in str(summary.get("error") or "").lower()
    assert summary["actions"] == []


def test_auto_refuses_mtf_ensure_false(tmp_path, monkeypatch):
    import radar.trade.auto_engine as ae

    monkeypatch.setattr(ae, "POS_PATH", tmp_path / "auto_positions.json")
    monkeypatch.setattr(ae, "STATE_PATH", tmp_path / "auto_trade_state.json")
    monkeypatch.setattr(ae, "LOG_PATH", tmp_path / "auto_trade_log.jsonl")

    eng = AutoTradeEngine(
        KuCoinManualTrader(),
        lambda: {
            "ok": True,
            "mtf_ensure_ok": False,
            "score_backend": "radar_native",
            "sectors": {
                "spot_alts": [
                    {
                        "symbol": "SOLUSDT",
                        "setup": "buy-watch",
                        "score_centi": 9000,
                        "price_i": 100_000_000_000,
                        "risk_flags": [],
                        "venue": "kucoin",
                        "market": "spot",
                    }
                ]
            },
        },
    )
    eng._persist_ok = True
    with eng._lock:
        eng._state["enabled"] = True
    summary = eng.run_once()
    assert summary["ok"] is False
    assert "mtf" in str(summary.get("error") or "").lower()
    assert summary["actions"] == []

def test_auto_stop_only_on_degraded_snap(tmp_path, monkeypatch):
    import radar.trade.auto_engine as ae

    monkeypatch.setattr(ae, "POS_PATH", tmp_path / "auto_positions.json")
    monkeypatch.setattr(ae, "STATE_PATH", tmp_path / "auto_trade_state.json")
    monkeypatch.setattr(ae, "LOG_PATH", tmp_path / "auto_trade_log.jsonl")

    eng = AutoTradeEngine(
        KuCoinManualTrader(),
        lambda: {
            "ok": False,
            "mtf_ensure_ok": False,
            "score_backend": "radar_native",
            "sectors": {},
        },
    )
    eng._persist_ok = True
    eng._positions = {
        "SOLUSDT": {
            "symbol": "SOLUSDT",
            "entry_price_i": 100_000_000_000,
            "base_size_i": 1_000_000,
            "quote_usdt_micros": 10_000_000,
        }
    }
    monkeypatch.setattr(
        eng._trader,
        "get_spot_mark_price_i",
        lambda sym: 80_000_000_000,  # -20% > default 12% stop
    )
    monkeypatch.setattr(
        eng,
        "_exec_sell",
        lambda *a, **k: {
            "ok": True,
            "dry_run": True,
            "side": "sell",
            "symbol": "SOLUSDT",
            "message": "stop",
        },
    )
    monkeypatch.setattr(
        ae,
        "load_config",
        lambda: {
            "auto_trade": {
                "dry_run": True,
                "stop_loss_bps": 1200,
                "max_sells_per_cycle": 5,
                "allowed_sectors": ["spot_alts"],
            },
            "native": {"prefer": False},
        },
    )
    summary = eng.run_once()
    assert summary["ok"] is False
    assert summary.get("mode") == "stop_only"
    assert any(a.get("side") == "sell" for a in summary["actions"])
    assert "SOLUSDT" not in eng._positions


def test_auto_stop_only_skips_buy_on_degraded(tmp_path, monkeypatch):
    import radar.trade.auto_engine as ae

    monkeypatch.setattr(ae, "POS_PATH", tmp_path / "auto_positions.json")
    monkeypatch.setattr(ae, "STATE_PATH", tmp_path / "auto_trade_state.json")
    monkeypatch.setattr(ae, "LOG_PATH", tmp_path / "auto_trade_log.jsonl")

    eng = AutoTradeEngine(
        KuCoinManualTrader(),
        lambda: {
            "ok": False,
            "sectors": {
                "spot_alts": [
                    {
                        "symbol": "NEWUSDT",
                        "setup": "buy-watch",
                        "score_centi": 9900,
                        "price_i": 100_000_000,
                        "risk_flags": [],
                        "venue": "kucoin",
                        "market": "spot",
                    }
                ]
            },
        },
    )
    eng._persist_ok = True
    eng._positions = {
        "SOLUSDT": {
            "symbol": "SOLUSDT",
            "entry_price_i": 100_000_000_000,
            "base_size_i": 1_000_000,
            "quote_usdt_micros": 10_000_000,
        }
    }
    monkeypatch.setattr(
        eng._trader,
        "get_spot_mark_price_i",
        lambda sym: 99_000_000_000,  # small drop — no stop
    )
    buys = []
    monkeypatch.setattr(
        eng,
        "_exec_buy",
        lambda *a, **k: buys.append(a) or {"ok": True, "side": "buy"},
    )
    monkeypatch.setattr(
        ae,
        "load_config",
        lambda: {
            "auto_trade": {
                "dry_run": True,
                "stop_loss_bps": 1200,
                "allowed_sectors": ["spot_alts"],
                "buy_setups": ["buy-watch"],
            },
            "native": {"prefer": False},
        },
    )
    summary = eng.run_once()
    assert summary.get("mode") == "stop_only"
    assert buys == []
    assert "SOLUSDT" in eng._positions

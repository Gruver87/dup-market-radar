"""Gating tests for opt-in auto_execute: default OFF, limits, kill-switch, no double-fire."""

from __future__ import annotations

import time

from radar.trade.auto_engine import AutoTradeEngine
from radar.trade.kucoin_manual import KuCoinManualTrader


def _patch_paths(ae, tmp_path, monkeypatch):
    monkeypatch.setattr(ae, "POS_PATH", tmp_path / "auto_positions.json")
    monkeypatch.setattr(ae, "STATE_PATH", tmp_path / "auto_trade_state.json")
    monkeypatch.setattr(ae, "LOG_PATH", tmp_path / "auto_trade_log.jsonl")


def _healthy_snap(symbol: str = "AAAUSDT", setup: str = "buy-watch"):
    return {
        "ok": True,
        "mtf_ensure_ok": True,
        "score_backend": "radar_native",
        "sectors": {
            "spot_alts": [
                {
                    "symbol": symbol,
                    "setup": setup,
                    "score_centi": 9000,
                    "price_i": 100_000_000_000,
                    "risk_flags": [],
                    "venue": "kucoin",
                    "market": "spot",
                }
            ]
        },
    }


def test_auto_execute_off_by_default(tmp_path, monkeypatch):
    import radar.trade.auto_engine as ae

    _patch_paths(ae, tmp_path, monkeypatch)
    eng = AutoTradeEngine(KuCoinManualTrader(), lambda: _healthy_snap())
    st = eng.status()
    assert st["enabled"] is False
    assert st["auto_execute"] is False
    assert st["armed"] is False
    assert st["kill_switch"] is False


def test_run_once_refuses_when_disabled(tmp_path, monkeypatch):
    import radar.trade.auto_engine as ae

    _patch_paths(ae, tmp_path, monkeypatch)
    buys = []
    eng = AutoTradeEngine(KuCoinManualTrader(), lambda: _healthy_snap())
    eng._persist_ok = True
    monkeypatch.setattr(
        eng,
        "_exec_buy",
        lambda *a, **k: buys.append(a) or {"ok": True, "side": "buy", "dry_run": True},
    )
    monkeypatch.setattr(
        ae,
        "load_config",
        lambda: {
            "auto_trade": {
                "dry_run": True,
                "enabled_default": False,
                "allowed_sectors": ["spot_alts"],
                "buy_setups": ["buy-watch"],
                "max_open_positions": 5,
                "max_buys_per_cycle": 2,
            },
            "native": {"prefer": False},
        },
    )
    summary = eng.run_once()
    assert summary["ok"] is False
    assert "off" in str(summary.get("error") or "").lower()
    assert buys == []


def test_kill_switch_disables_and_blocks_enable(tmp_path, monkeypatch):
    import radar.trade.auto_engine as ae

    _patch_paths(ae, tmp_path, monkeypatch)
    monkeypatch.setattr(
        ae,
        "load_config",
        lambda: {
            "auto_trade": {"dry_run": True, "enabled_default": False},
            "native": {"prefer": False},
        },
    )
    eng = AutoTradeEngine(KuCoinManualTrader(), lambda: _healthy_snap())
    eng._persist_ok = True
    eng.set_enabled(True)
    assert eng.status()["enabled"] is True

    st = eng.trip_kill_switch(reason="test_kill")
    assert st["enabled"] is False
    assert st["auto_execute"] is False
    assert st["kill_switch"] is True
    assert st["armed"] is False
    assert "test_kill" in str(st.get("kill_reason") or "")

    try:
        eng.set_enabled(True)
        assert False, "expected RuntimeError while kill latched"
    except RuntimeError as exc:
        assert "kill" in str(exc).lower()

    # Cycle must refuse while kill latched even if someone flips state
    with eng._lock:
        eng._state["enabled"] = True  # simulate inconsistent state
    summary = eng.run_once()
    assert summary["ok"] is False
    assert "kill" in str(summary.get("error") or "").lower()

    cleared = eng.clear_kill_switch()
    assert cleared["kill_switch"] is False
    assert cleared["enabled"] is False  # clear does not enable
    eng.set_enabled(True)
    assert eng.status()["enabled"] is True


def test_signal_cooldown_blocks_duplicate_buy(tmp_path, monkeypatch):
    import radar.trade.auto_engine as ae

    _patch_paths(ae, tmp_path, monkeypatch)
    buys = []

    def fake_buy(sym, quote, price_i, sector, dry):
        buys.append(sym)
        return {
            "ok": True,
            "dry_run": True,
            "side": "buy",
            "symbol": sym,
            "message": "dry",
        }

    eng = AutoTradeEngine(KuCoinManualTrader(), lambda: _healthy_snap("AAAUSDT"))
    eng._persist_ok = True
    with eng._lock:
        eng._state["enabled"] = True
    monkeypatch.setattr(eng, "_exec_buy", fake_buy)
    monkeypatch.setattr(
        ae,
        "load_config",
        lambda: {
            "auto_trade": {
                "dry_run": True,
                "allowed_sectors": ["spot_alts"],
                "buy_setups": ["buy-watch"],
                "max_open_positions": 5,
                "max_buys_per_cycle": 2,
                "max_daily_buys": 20,
                "max_daily_notional_usdt_micros": 200_000_000,
                "signal_cooldown_sec": 900,
                "quote_usdt_micros_per_buy": 10_000_000,
                "fill_reconcile": False,
                "require_no_risk_flags": True,
            },
            "native": {"prefer": False},
        },
    )
    s1 = eng.run_once()
    assert any(a.get("side") == "buy" and a.get("ok") for a in s1["actions"])
    assert "AAAUSDT" in eng._positions
    assert buys == ["AAAUSDT"]

    # Drop position but keep last_fire cooldown → second buy must skip
    with eng._lock:
        eng._positions.clear()
        eng._save_positions_locked()
    buys.clear()
    s2 = eng.run_once()
    skipped = [a for a in s2["actions"] if a.get("skipped") and a.get("side") == "buy"]
    assert skipped, s2["actions"]
    assert "cooldown" in str(skipped[0].get("message") or "").lower()
    assert buys == []


def test_max_daily_buys_gate(tmp_path, monkeypatch):
    import radar.trade.auto_engine as ae

    _patch_paths(ae, tmp_path, monkeypatch)
    buys = []
    eng = AutoTradeEngine(KuCoinManualTrader(), lambda: _healthy_snap("BBBUSDT"))
    eng._persist_ok = True
    with eng._lock:
        eng._state["enabled"] = True
        eng._state["daily"] = {
            "day_utc": time.strftime("%Y-%m-%d", time.gmtime()),
            "buys": 1,
            "sells": 0,
            "buy_notional_micros": 10_000_000,
        }
    monkeypatch.setattr(
        eng,
        "_exec_buy",
        lambda *a, **k: buys.append(a[0])
        or {"ok": True, "dry_run": True, "side": "buy", "symbol": a[0]},
    )
    monkeypatch.setattr(
        ae,
        "load_config",
        lambda: {
            "auto_trade": {
                "dry_run": True,
                "allowed_sectors": ["spot_alts"],
                "buy_setups": ["buy-watch"],
                "max_open_positions": 5,
                "max_buys_per_cycle": 2,
                "max_daily_buys": 1,
                "max_daily_notional_usdt_micros": 500_000_000,
                "signal_cooldown_sec": 0,
                "quote_usdt_micros_per_buy": 10_000_000,
                "fill_reconcile": False,
            },
            "native": {"prefer": False},
        },
    )
    summary = eng.run_once()
    skipped = [a for a in summary["actions"] if a.get("skipped")]
    assert skipped
    assert "max_daily_buys" in str(skipped[0].get("message") or "")
    assert buys == []


def test_max_daily_notional_gate(tmp_path, monkeypatch):
    import radar.trade.auto_engine as ae

    _patch_paths(ae, tmp_path, monkeypatch)
    buys = []
    eng = AutoTradeEngine(KuCoinManualTrader(), lambda: _healthy_snap("CCCUSDT"))
    eng._persist_ok = True
    with eng._lock:
        eng._state["enabled"] = True
        eng._state["daily"] = {
            "day_utc": time.strftime("%Y-%m-%d", time.gmtime()),
            "buys": 0,
            "sells": 0,
            "buy_notional_micros": 15_000_000,
        }
    monkeypatch.setattr(
        eng,
        "_exec_buy",
        lambda *a, **k: buys.append(1)
        or {"ok": True, "dry_run": True, "side": "buy", "symbol": a[0]},
    )
    monkeypatch.setattr(
        ae,
        "load_config",
        lambda: {
            "auto_trade": {
                "dry_run": True,
                "allowed_sectors": ["spot_alts"],
                "buy_setups": ["buy-watch"],
                "max_open_positions": 5,
                "max_buys_per_cycle": 2,
                "max_daily_buys": 20,
                "max_daily_notional_usdt_micros": 20_000_000,  # 20 USDT
                "signal_cooldown_sec": 0,
                "quote_usdt_micros_per_buy": 10_000_000,  # would make 25
                "fill_reconcile": False,
            },
            "native": {"prefer": False},
        },
    )
    summary = eng.run_once()
    skipped = [a for a in summary["actions"] if a.get("skipped")]
    assert skipped
    assert "max_daily_notional" in str(skipped[0].get("message") or "")
    assert buys == []


def test_armed_requires_live_keys_and_not_dry(tmp_path, monkeypatch):
    import radar.trade.auto_engine as ae

    _patch_paths(ae, tmp_path, monkeypatch)
    eng = AutoTradeEngine(KuCoinManualTrader(), lambda: {})
    eng._persist_ok = True
    with eng._lock:
        eng._state["enabled"] = True
    monkeypatch.setattr(
        ae,
        "load_config",
        lambda: {"auto_trade": {"dry_run": True}, "native": {"prefer": False}},
    )
    st = eng.status()
    assert st["auto_execute"] is True
    assert st["armed"] is False  # dry_run blocks armed
    assert st["dry_run"] is True

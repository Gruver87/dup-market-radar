"""Auto-trade cycle single-flight + graceful stop."""

import threading
import time

from radar.trade.auto_engine import AutoTradeEngine
from radar.trade.kucoin_manual import KuCoinManualTrader


def test_concurrent_run_once_soft_refuses(tmp_path, monkeypatch):
    import radar.trade.auto_engine as ae

    monkeypatch.setattr(ae, "POS_PATH", tmp_path / "auto_positions.json")
    monkeypatch.setattr(ae, "STATE_PATH", tmp_path / "auto_trade_state.json")
    monkeypatch.setattr(ae, "LOG_PATH", tmp_path / "auto_trade_log.jsonl")

    eng = AutoTradeEngine(KuCoinManualTrader(), lambda: {"sectors": {}})
    eng._persist_ok = True
    eng._positions = {}

    entered = threading.Event()
    release = threading.Event()

    def slow_unlocked():
        entered.set()
        release.wait(timeout=5)
        return {"at": time.time(), "ok": True, "actions": [], "open_positions": 0}

    monkeypatch.setattr(eng, "_run_once_unlocked", slow_unlocked)

    results = []

    def caller():
        results.append(eng.run_once())

    t1 = threading.Thread(target=caller)
    t1.start()
    assert entered.wait(timeout=2)
    second = eng.run_once()
    assert second["ok"] is False
    assert "in flight" in second["error"].lower()
    release.set()
    t1.join(timeout=3)
    assert len(results) == 1
    assert results[0]["ok"] is True


def test_stop_loop_joins_thread(tmp_path, monkeypatch):
    import radar.trade.auto_engine as ae

    monkeypatch.setattr(ae, "POS_PATH", tmp_path / "auto_positions.json")
    monkeypatch.setattr(ae, "STATE_PATH", tmp_path / "auto_trade_state.json")
    monkeypatch.setattr(ae, "LOG_PATH", tmp_path / "auto_trade_log.jsonl")

    eng = AutoTradeEngine(KuCoinManualTrader(), lambda: {"sectors": {}})
    with eng._lock:
        eng._state["enabled"] = False
    eng.start()
    assert eng._thread is not None and eng._thread.is_alive()
    eng.stop_loop(join_timeout_sec=3.0)
    assert eng._thread is None or not eng._thread.is_alive()

"""Self-recovery supervisor — watchdog + network return detection."""

from __future__ import annotations

import time

from radar.recovery import RecoverySupervisor


def test_network_recovered_after_consecutive_fails(monkeypatch):
    calls = {"n": 0}
    events = []

    def snap():
        calls["n"] += 1
        # First two fail-ish (ok=false), then ok
        if calls["n"] < 3:
            return {"ok": False, "mtf_ensure_ok": False, "universe_size": 0}
        return {"ok": True, "mtf_ensure_ok": True, "universe_size": 42}

    def on_rec(payload):
        events.append(payload)

    monkeypatch.setattr(
        "radar.recovery.load_config",
        lambda: {
            "recovery": {
                "enabled": True,
                "background_scan": True,
                "restart_auto_thread": False,
                "restart_telegram": False,
                "notify_on_network_recovered": True,
                "tick_sec": 5,
                "max_restarts_per_hour": 20,
                "restart_backoff_ms": 100,
            },
            "snapshot_refresh_ms": 5_000,
        },
    )

    sup = RecoverySupervisor(
        snapshot_fn=snap,
        ensure_auto=lambda: True,
        ensure_telegram=lambda: True,
        auto_alive=lambda: True,
        telegram_wanted=lambda: False,
        telegram_alive=lambda: True,
        on_network_recovered=on_rec,
    )
    # Force due scans by clearing last timestamps via direct ticks
    cfg = {
        "enabled": True,
        "background_scan": True,
        "restart_auto_thread": False,
        "restart_telegram": False,
        "notify_on_network_recovered": True,
    }
    sup._last_scan_ok_ms = None
    sup._last_scan_fail_ms = None
    sup._tick(cfg)
    assert sup.status()["consecutive_scan_fails"] == 1
    # Bypass gap: pretend last fail was long ago
    with sup._lock:
        sup._last_scan_fail_ms = int(time.time() * 1000) - 60_000
    sup._tick(cfg)
    assert sup.status()["consecutive_scan_fails"] == 2
    with sup._lock:
        sup._last_scan_fail_ms = int(time.time() * 1000) - 60_000
    sup._tick(cfg)
    st = sup.status()
    assert st["consecutive_scan_fails"] == 0
    assert st["stats"]["network_recoveries"] == 1
    assert len(events) == 1
    assert events[0]["prev_consecutive_fails"] == 2
    assert events[0]["ok"] is True


def test_auto_restart_when_dead(monkeypatch):
    restarts = {"n": 0}

    monkeypatch.setattr(
        "radar.recovery.load_config",
        lambda: {
            "recovery": {
                "enabled": True,
                "background_scan": False,
                "restart_auto_thread": True,
                "restart_telegram": False,
                "max_restarts_per_hour": 20,
                "restart_backoff_ms": 50,
            },
            "snapshot_refresh_ms": 45_000,
        },
    )

    alive = {"v": False}

    def ensure():
        restarts["n"] += 1
        alive["v"] = True
        return True

    sup = RecoverySupervisor(
        snapshot_fn=lambda: {"ok": True},
        ensure_auto=ensure,
        ensure_telegram=lambda: True,
        auto_alive=lambda: alive["v"],
        telegram_wanted=lambda: False,
        telegram_alive=lambda: True,
    )
    cfg = {
        "enabled": True,
        "background_scan": False,
        "restart_auto_thread": True,
        "restart_telegram": False,
        "restart_backoff_ms": 50,
        "max_restarts_per_hour": 20,
    }
    sup._tick(cfg)
    assert restarts["n"] == 1
    assert alive["v"] is True
    assert sup.status()["stats"]["auto_restarts"] == 1
    # Second tick — already alive, no restart
    sup._tick(cfg)
    assert restarts["n"] == 1


def test_restart_budget_soft_refuse(monkeypatch):
    monkeypatch.setattr(
        "radar.recovery.load_config",
        lambda: {
            "recovery": {
                "enabled": True,
                "background_scan": False,
                "restart_auto_thread": True,
                "restart_telegram": False,
                "max_restarts_per_hour": 2,
                "restart_backoff_ms": 10,
            },
            "snapshot_refresh_ms": 45_000,
        },
    )
    n = {"c": 0}

    def ensure():
        n["c"] += 1
        return True

    # Always report dead so every tick tries restart
    sup = RecoverySupervisor(
        snapshot_fn=lambda: {"ok": True},
        ensure_auto=ensure,
        ensure_telegram=lambda: True,
        auto_alive=lambda: False,
        telegram_wanted=lambda: False,
        telegram_alive=lambda: True,
    )
    cfg = {
        "enabled": True,
        "background_scan": False,
        "restart_auto_thread": True,
        "restart_telegram": False,
        "restart_backoff_ms": 10,
        "max_restarts_per_hour": 2,
    }
    sup._tick(cfg)
    sup._tick(cfg)
    assert n["c"] == 2
    sup._tick(cfg)
    assert n["c"] == 2  # budget exhausted
    assert sup.status()["stats"]["restart_refused_budget"] >= 1

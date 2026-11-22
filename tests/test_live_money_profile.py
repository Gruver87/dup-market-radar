"""RADAR_PROFILE=live-money overlay — spot auto only, futures excluded."""

from __future__ import annotations

import pytest

from radar.config import load_config
from radar.trade.auto_engine import FUTURES_BLOCK



@pytest.fixture(autouse=True)
def _clear_profile(monkeypatch):
    monkeypatch.delenv("RADAR_PROFILE", raising=False)
    monkeypatch.delenv("RADAR_CONFIG", raising=False)
    monkeypatch.delenv("RADAR_CONFIG_PATH", raising=False)


def test_live_money_overlay_spot_only_small_size(monkeypatch):
    monkeypatch.setenv("RADAR_PROFILE", "live-money")
    # Isolate from CI python-lab (RADAR_NATIVE_*=0) — live-money YAML sets prefer/fail_closed.
    monkeypatch.delenv("RADAR_NATIVE_PREFER", raising=False)
    monkeypatch.delenv("RADAR_NATIVE_FAIL_CLOSED", raising=False)
    monkeypatch.delenv("RADAR_DEPLOYMENT_MODE", raising=False)
    cfg = load_config()
    assert cfg.get("_radar_profile") == "live-money"
    assert str(cfg.get("deployment_mode")).lower() == "prod"
    auto = cfg.get("auto_trade") or {}
    assert auto.get("dry_run") is False
    assert auto.get("enabled_default") is False
    sectors = list(auto.get("allowed_sectors") or [])
    assert "spot_alts" in sectors
    assert not (set(sectors) & FUTURES_BLOCK)
    assert str(auto.get("sizing_mode") or "") == "shared_pool"
    assert int(auto.get("max_open_positions") or 0) == 4
    assert int(auto.get("max_daily_buys") or 0) == 0  # unlimited for measurement
    assert int(auto.get("max_daily_notional_usdt_micros") or 0) == 0
    assert int(auto.get("max_buys_per_cycle") or 0) == 0
    assert int(auto.get("max_sells_per_cycle") or 0) == 0
    assert int(auto.get("min_score_centi") or 0) == 4500
    assert int(auto.get("max_chase_change_bps_1h") or 0) == 500
    assert int(auto.get("fee_bps_per_side") or 0) == 10
    assert int(auto.get("loss_streak_pause_buys") or 0) == 3
    assert int(auto.get("trailing_stop_bps") or 0) == 200
    assert int(auto.get("trailing_offset_bps") or 0) == 300
    assert int(auto.get("fade_min_abs_bps") or 0) == 100
    assert int(auto.get("max_hold_sec") or 0) == 14400
    ladder = auto.get("roi_ladder") or []
    assert isinstance(ladder, list) and len(ladder) >= 4
    setups = list(auto.get("buy_setups") or [])
    assert "buy-watch" in setups
    assert "long" not in setups
    assert "BTCUSDT" in (auto.get("symbol_blacklist") or [])
    assert int((cfg.get("api") or {}).get("max_order_quote_usdt_micros") or 0) == 500_000_000
    assert (cfg.get("secrets") or {}).get("backend") == "env"
    native = cfg.get("native") or {}
    assert native.get("prefer") is True
    assert native.get("fail_closed") is True


def test_default_stays_dry_run_lab(monkeypatch):
    monkeypatch.delenv("RADAR_DEPLOYMENT_MODE", raising=False)
    cfg = load_config()
    assert cfg.get("_radar_profile") in (None, "")
    assert (cfg.get("auto_trade") or {}).get("dry_run") is True
    assert str(cfg.get("deployment_mode")).lower() == "lab"

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
    assert str(auto.get("sizing_mode") or "") == "equal_sleeves"
    assert int(auto.get("max_open_positions") or 0) == 4
    assert int(auto.get("max_daily_buys") or 0) == 12
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

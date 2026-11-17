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
    cfg = load_config()
    assert cfg.get("_radar_profile") == "live-money"
    assert str(cfg.get("deployment_mode")).lower() == "prod"
    auto = cfg.get("auto_trade") or {}
    assert auto.get("dry_run") is False
    assert auto.get("enabled_default") is False
    sectors = list(auto.get("allowed_sectors") or [])
    assert "spot_alts" in sectors
    assert not (set(sectors) & FUTURES_BLOCK)
    assert int(auto.get("quote_usdt_micros_per_buy") or 0) == 5_000_000
    assert (cfg.get("secrets") or {}).get("backend") == "env"
    native = cfg.get("native") or {}
    assert native.get("prefer") is True
    assert native.get("fail_closed") is True


def test_default_stays_dry_run_lab():
    cfg = load_config()
    assert cfg.get("_radar_profile") in (None, "")
    assert (cfg.get("auto_trade") or {}).get("dry_run") is True
    assert str(cfg.get("deployment_mode")).lower() == "lab"

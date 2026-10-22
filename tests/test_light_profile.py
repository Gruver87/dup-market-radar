"""RADAR_PROFILE=light overlay (0.2.87)."""

import os

import pytest

from radar.config import ConfigError, load_config, validate_config


def test_default_profile_unchanged():
    os.environ.pop("RADAR_PROFILE", None)
    os.environ.pop("RADAR_CONFIG", None)
    os.environ.pop("RADAR_CONFIG_PATH", None)
    cfg = load_config()
    assert "kucoin" in cfg["venues"]
    assert "binance" in cfg["venues"]
    assert cfg.get("_radar_profile") is None


def test_light_profile_overlay(monkeypatch):
    monkeypatch.setenv("RADAR_PROFILE", "light")
    monkeypatch.delenv("RADAR_CONFIG", raising=False)
    monkeypatch.delenv("RADAR_CONFIG_PATH", raising=False)
    cfg = load_config()
    assert cfg.get("_radar_profile") == "light"
    assert cfg["venues"] == ["kucoin"]
    assert int((cfg.get("ingest") or {}).get("max_kline_gets") or 0) == 72
    assert int((cfg.get("sectors") or {}).get("memes", {}).get("poll_sec") or 0) == 300
    # Full default keys still present after merge.
    assert (cfg.get("native") or {}).get("prefer") is True
    validate_config(cfg)


def test_light_still_requires_kucoin(monkeypatch):
    monkeypatch.setenv("RADAR_PROFILE", "light")
    cfg = load_config()
    cfg = dict(cfg)
    cfg["venues"] = ["binance"]
    with pytest.raises(ConfigError, match="kucoin"):
        validate_config(cfg)

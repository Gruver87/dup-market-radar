"""display_ensure_wall_ms config bounds (0.2.85)."""

import pytest

from radar.config import ConfigError, load_config, validate_config


def test_default_display_ensure_wall_valid():
    cfg = load_config()
    assert int((cfg.get("ingest") or {}).get("display_ensure_wall_ms") or 0) == 1500
    validate_config(cfg)


def test_rejects_display_ensure_wall_too_low():
    cfg = load_config()
    cfg = dict(cfg)
    cfg["ingest"] = dict(cfg.get("ingest") or {})
    cfg["ingest"]["display_ensure_wall_ms"] = 100
    with pytest.raises(ConfigError, match="display_ensure_wall_ms"):
        validate_config(cfg)

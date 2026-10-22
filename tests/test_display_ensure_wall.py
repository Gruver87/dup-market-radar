"""display ensure wall config + low-board policy (0.2.85 / 0.2.86)."""

import pytest

from radar.config import ConfigError, load_config, validate_config
from radar.pipeline import _display_ensure_wall_ms


def test_default_display_ensure_wall_valid():
    cfg = load_config()
    ingest = cfg.get("ingest") or {}
    assert int(ingest.get("display_ensure_wall_ms") or 0) == 1500
    assert int(ingest.get("display_ensure_wall_low_ms") or 0) == 4000
    assert float(ingest.get("display_ensure_board_low") or 0) == 0.5
    validate_config(cfg)


def test_rejects_display_ensure_wall_too_low():
    cfg = load_config()
    cfg = dict(cfg)
    cfg["ingest"] = dict(cfg.get("ingest") or {})
    cfg["ingest"]["display_ensure_wall_ms"] = 100
    with pytest.raises(ConfigError, match="display_ensure_wall_ms"):
        validate_config(cfg)


def test_display_wall_low_board_picks_longer():
    ingest = {
        "display_ensure_wall_ms": 1500,
        "display_ensure_wall_low_ms": 4000,
        "display_ensure_board_low": 0.5,
    }
    wall, reason = _display_ensure_wall_ms(ingest, 0.2, 0.9)
    assert wall == 4000
    assert reason == "low_board"


def test_display_wall_ok_board_picks_base():
    ingest = {
        "display_ensure_wall_ms": 1500,
        "display_ensure_wall_low_ms": 4000,
        "display_ensure_board_low": 0.5,
    }
    wall, reason = _display_ensure_wall_ms(ingest, 0.7, 0.8)
    assert wall == 1500
    assert reason == "base"


def test_display_wall_unknown_board_uses_low():
    ingest = {
        "display_ensure_wall_ms": 1500,
        "display_ensure_wall_low_ms": 4000,
    }
    wall, reason = _display_ensure_wall_ms(ingest, None, None)
    assert wall == 4000
    assert reason == "unknown_board"

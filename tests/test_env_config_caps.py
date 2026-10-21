"""Dotenv / config / persist write size caps."""

import pytest

from radar.config import ConfigError, load_config
from radar.persist import PersistError, atomic_write_json
from radar.trade.env_load import load_dotenv


def test_dotenv_refuses_huge_file(tmp_path, monkeypatch, caplog):
    env = tmp_path / ".env"
    env.write_bytes(b"A=1\n" * 20_000)  # >64 KiB
    load_dotenv(env)
    assert "RADAR_TEST_HUGE" not in __import__("os").environ
    assert any("too large" in r.message for r in caplog.records)


def test_dotenv_loads_small(tmp_path, monkeypatch):
    monkeypatch.delenv("RADAR_TEST_SMALL_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text("RADAR_TEST_SMALL_KEY=hello_lab\n", encoding="utf-8")
    load_dotenv(env)
    assert __import__("os").environ.get("RADAR_TEST_SMALL_KEY") == "hello_lab"
    monkeypatch.delenv("RADAR_TEST_SMALL_KEY", raising=False)


def test_config_too_large(tmp_path):
    p = tmp_path / "huge.yaml"
    p.write_bytes(b"x: " + (b"a" * 2_000_000))
    with pytest.raises(ConfigError, match="too large"):
        load_config(p)


def test_atomic_write_refuses_huge(tmp_path):
    path = tmp_path / "big.json"
    with pytest.raises(PersistError, match="too large"):
        atomic_write_json(path, {"pad": "x" * 9_000_000}, backup_keep=0)

"""AllowlistHttp stale-OK + last-good (0.2.86)."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from radar.config import ConfigError, load_config, validate_config
from radar.http_client import AllowlistHttp


def test_default_kucoin_stale_ok_valid():
    cfg = load_config()
    assert int((cfg.get("ingest") or {}).get("kucoin_stale_ok_ms") or 0) == 90_000
    assert int((cfg.get("ingest") or {}).get("secondary_venue_wall_ms") or 0) == 3500
    validate_config(cfg)


def test_rejects_kucoin_stale_ok_too_low():
    cfg = load_config()
    cfg = dict(cfg)
    cfg["ingest"] = dict(cfg.get("ingest") or {})
    cfg["ingest"]["kucoin_stale_ok_ms"] = 100
    with pytest.raises(ConfigError, match="kucoin_stale_ok_ms"):
        validate_config(cfg)


def test_stale_ok_serves_last_good_without_network():
    http = AllowlistHttp(
        {"api.kucoin.com"},
        timeout_ms=2000,
        cache_ttl_ms=50,
        retries=0,
    )
    try:
        url = "https://api.kucoin.com/api/v1/market/allTickers"
        body = {"data": {"ticker": [{"symbol": "BTC-USDT"}]}}
        # Seed last-good via cache remember path.
        now = int(time.time() * 1000)
        with http._lock:
            http._cache[url] = (now, body)
            http._remember_unlocked(url, now, body)
        # Expire fresh TTL.
        time.sleep(0.06)
        # Network would fail — mock client to prove we never call it.
        http._client = MagicMock()
        http._client.get.side_effect = RuntimeError("network should not run")
        out = http.get_json(url, stale_ok_ms=90_000)
        assert out == body
        assert http.stats["stale_ok_hits"] == 1
        http._client.get.assert_not_called()
    finally:
        http.close()


def test_stale_on_error_fallback():
    http = AllowlistHttp(
        {"api.kucoin.com"},
        timeout_ms=2000,
        cache_ttl_ms=50,
        retries=0,
    )
    try:
        url = "https://api.kucoin.com/api/v1/market/allTickers"
        body = {"ok": True}
        now = int(time.time() * 1000)
        with http._lock:
            http._remember_unlocked(url, now, body)
        time.sleep(0.06)
        http._client = MagicMock()
        http._client.get.side_effect = ConnectionError("down")
        out = http.get_json(url, stale_on_error_ms=180_000)
        assert out == body
        assert http.stats["stale_error_fallback"] == 1
    finally:
        http.close()

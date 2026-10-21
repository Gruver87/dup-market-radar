from __future__ import annotations

import pytest

from radar.http_client import AllowlistHttp


def test_allowlist_refuses_unknown_host():
    http = AllowlistHttp({"api.binance.com"}, timeout_sec=2, cache_ttl_sec=1)
    with pytest.raises(PermissionError):
        http.get_json("https://evil.example/x")
    http.close()


def test_allowlist_refuses_http_scheme():
    http = AllowlistHttp({"api.binance.com"}, timeout_sec=2, cache_ttl_sec=1)
    with pytest.raises(PermissionError, match="https only"):
        http.get_json("http://api.binance.com/x")
    assert http.stats["scheme_refused"] >= 1
    http.close()


def test_error_ring_bounded():
    http = AllowlistHttp(
        {"api.binance.com"}, timeout_sec=2, cache_ttl_sec=1, max_error_log=8
    )
    for _ in range(20):
        with pytest.raises(PermissionError):
            http.get_json("https://evil.example/x")
    assert len(http.errors) == 8
    http.close()

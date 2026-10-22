"""Process-lifetime HTTP pool reuses client for same fingerprint."""

from radar.http_pool import acquire_http, close_http_pool, stats_delta


def test_acquire_reuses_same_client():
    close_http_pool()
    a = acquire_http({"api.kucoin.com"}, timeout_ms=5000, cache_ttl_ms=1000)
    b = acquire_http({"api.kucoin.com"}, timeout_ms=5000, cache_ttl_ms=1000)
    assert a is b
    close_http_pool()


def test_stats_delta():
    close_http_pool()
    http = acquire_http({"api.kucoin.com"}, timeout_ms=5000, cache_ttl_ms=1000)
    baseline = dict(http.stats)
    http.stats["gets"] = int(http.stats.get("gets") or 0) + 3
    d = stats_delta(http, baseline)
    assert d["gets"] == 3
    close_http_pool()


def test_fingerprint_change_recreates():
    close_http_pool()
    a = acquire_http({"api.kucoin.com"}, timeout_ms=5000, cache_ttl_ms=1000)
    b = acquire_http({"api.kucoin.com"}, timeout_ms=6000, cache_ttl_ms=1000)
    assert a is not b
    close_http_pool()

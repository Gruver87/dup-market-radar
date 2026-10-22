"""Process-lifetime MTF reuse cache (0.2.87)."""

from radar.ingest.mtf_cache import MtfReuseCache


def test_hydrate_and_remember_roundtrip():
    cache = MtfReuseCache()
    src = {
        "symbol": "SOLUSDT",
        "change_bps_15m": 120,
        "change_bps_1h": 80,
        "vol_vs_median_bps": 50,
        "mtf_attached_at_ms": 10**12,
    }
    assert cache.remember_rows([src], market="spot") == 1
    dst = {"symbol": "SOLUSDT", "venue": "kucoin"}
    # Force "now" via future stamp so age is small relative to huge ttl.
    src["mtf_attached_at_ms"] = int(__import__("time").time() * 1000)
    cache.remember_rows([src], market="spot")
    hits = cache.hydrate_rows([dst], market="spot", ttl_ms=450_000)
    assert hits == 1
    assert dst["change_bps_15m"] == 120
    assert dst["change_bps_1h"] == 80


def test_hydrate_skips_when_already_filled():
    cache = MtfReuseCache()
    now = int(__import__("time").time() * 1000)
    cache.remember_rows(
        [
            {
                "symbol": "BTCUSDT",
                "change_bps_15m": 1,
                "mtf_attached_at_ms": now,
            }
        ],
        market="spot",
    )
    row = {"symbol": "BTCUSDT", "change_bps_15m": 99}
    assert cache.hydrate_rows([row], market="spot", ttl_ms=450_000) == 0
    assert row["change_bps_15m"] == 99


def test_hydrate_respects_ttl():
    cache = MtfReuseCache()
    cache.remember_rows(
        [
            {
                "symbol": "ETHUSDT",
                "change_bps_15m": 5,
                "mtf_attached_at_ms": 1,  # ancient
            }
        ],
        market="futures",
    )
    row = {"symbol": "ETHUSDT"}
    assert cache.hydrate_rows([row], market="futures", ttl_ms=60_000) == 0
    assert row.get("change_bps_15m") is None

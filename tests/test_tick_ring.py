"""Unit tests for additive native WS tick ring (no live network)."""

from __future__ import annotations

from radar.ingest.tick_ring import NativeWsFeed, configure_from_cfg, overlay_status


def test_configure_disabled_by_default() -> None:
    opts = configure_from_cfg({"ingest": {}})
    assert opts["enabled"] is False


def test_overlay_disabled() -> None:
    st = overlay_status({"ingest": {"native_ws": {"enabled": False}}})
    assert st["enabled"] is False
    assert st["replaces_http_poll"] is False
    assert st["ok"] is False


def test_ring_push_and_status() -> None:
    feed = NativeWsFeed(venue="binance", symbols=["BTCUSDT"], ring_size=64, stale_ms=60_000)
    feed.push_for_test(
        {
            "venue": "binance",
            "symbol": "BTCUSDT",
            "price_i": 6_401_250_000_000,
            "qty_i": 100_000,
            "ts_ms": 1_700_000_000_000,
            "side": "buy",
        }
    )
    st = feed.status()
    assert st["enabled"] is True
    assert st["ticks_total"] == 1
    assert st["replaces_http_poll"] is False
    assert "BTCUSDT" in st["last_by_symbol"]
    assert st["last_by_symbol"]["BTCUSDT"]["price_i"] == 6_401_250_000_000

"""Unit tests for additive native WS tick ring (no live network)."""

from __future__ import annotations

from radar.ingest.tick_ring import (
    NativeWsFeed,
    _normalize_venues,
    _symbols_by_venue,
    configure_from_cfg,
    overlay_status,
)


def test_configure_disabled_by_default() -> None:
    opts = configure_from_cfg({"ingest": {}})
    assert opts["enabled"] is False


def test_overlay_disabled() -> None:
    st = overlay_status({"ingest": {"native_ws": {"enabled": False}}})
    assert st["enabled"] is False
    assert st["replaces_http_poll"] is False
    assert st["ok"] is False


def test_normalize_venues_multi() -> None:
    assert _normalize_venues("binance", ["bybit", "binance", "bybit"]) == [
        "bybit",
        "binance",
    ]
    assert _normalize_venues("kucoin", None) == ["kucoin"]
    assert _normalize_venues("nope", ["mexc", "binance"]) == ["mexc", "binance"]
    assert _normalize_venues("nope", ["gate", "binance"]) == ["binance"]


def test_configure_venues_list() -> None:
    opts = configure_from_cfg(
        {
            "ingest": {
                "native_ws": {
                    "enabled": True,
                    "venue": "binance",
                    "venues": ["binance", "bybit"],
                }
            }
        }
    )
    assert opts["venues"] == ["binance", "bybit"]
    assert opts["venue"] == "binance"


def test_symbols_by_venue_override() -> None:
    by = _symbols_by_venue(
        ["binance", "bybit"],
        ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        {"bybit": ["BTCUSDT", "ETHUSDT"]},
    )
    assert by["binance"] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert by["bybit"] == ["BTCUSDT", "ETHUSDT"]
    opts = configure_from_cfg(
        {
            "ingest": {
                "native_ws": {
                    "enabled": True,
                    "venues": ["binance", "bybit"],
                    "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
                    "venue_symbols": {"bybit": ["BTCUSDT"]},
                }
            }
        }
    )
    assert opts["symbols_by_venue"]["bybit"] == ["BTCUSDT"]
    assert opts["symbols"] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert "SOLUSDT" in opts["symbols_union"]
    feed = NativeWsFeed(
        venues=["binance", "bybit"],
        symbols=["BTCUSDT"],
        venue_symbols={"bybit": ["BTCUSDT", "ETHUSDT"]},
    )
    assert feed.symbols_by_venue["binance"] == ["BTCUSDT"]
    assert feed.symbols_by_venue["bybit"] == ["BTCUSDT", "ETHUSDT"]
    assert feed.symbols == ["BTCUSDT", "ETHUSDT"]


def test_ring_push_and_status() -> None:
    feed = NativeWsFeed(
        venue="binance",
        venues=["binance", "bybit"],
        symbols=["BTCUSDT"],
        ring_size=64,
        stale_ms=60_000,
    )
    assert feed.venues == ["binance", "bybit"]
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
    feed.push_for_test(
        {
            "venue": "bybit",
            "symbol": "BTCUSDT",
            "price_i": 6_401_260_000_000,
            "qty_i": 50_000,
            "ts_ms": 1_700_000_000_100,
            "side": "sell",
        }
    )
    st = feed.status()
    assert st["enabled"] is True
    assert st["ticks_total"] == 2
    assert st["venues"] == ["binance", "bybit"]
    assert st["replaces_http_poll"] is False
    assert "BTCUSDT" in st["last_by_symbol"]
    assert st["last_by_symbol"]["BTCUSDT"]["price_i"] == 6_401_260_000_000
    assert st["last_by_symbol"]["BTCUSDT"]["venue"] == "bybit"

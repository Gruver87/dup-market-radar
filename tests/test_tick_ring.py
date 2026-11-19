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
    assert _normalize_venues(
        "binance", ["binance", "bybit", "mexc", "kucoin"]
    ) == ["binance", "bybit", "mexc", "kucoin"]
    assert (
        len(_normalize_venues("binance", ["binance", "bybit", "mexc", "kucoin", "x"]))
        == 4
    )


def test_configure_venues_list() -> None:
    opts = configure_from_cfg(
        {
            "ingest": {
                "native_ws": {
                    "enabled": True,
                    "venue": "binance",
                    "venues": ["binance", "bybit", "kucoin", "mexc"],
                }
            }
        }
    )
    assert opts["venues"] == ["binance", "bybit", "kucoin", "mexc"]
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
    import time

    now = int(time.time() * 1000)
    feed = NativeWsFeed(
        venue="binance",
        venues=["binance", "bybit"],
        symbols=["BTCUSDT"],
        ring_size=64,
        stale_ms=60_000,
        price_venue_policy="primary",
    )
    assert feed.venues == ["binance", "bybit"]
    feed.push_for_test(
        {
            "venue": "binance",
            "symbol": "BTCUSDT",
            "price_i": 6_401_250_000_000,
            "qty_i": 100_000,
            "ts_ms": now,
            "side": "buy",
        }
    )
    feed.push_for_test(
        {
            "venue": "bybit",
            "symbol": "BTCUSDT",
            "price_i": 6_401_260_000_000,
            "qty_i": 50_000,
            "ts_ms": now + 100,
            "side": "sell",
        }
    )
    st = feed.status()
    assert st["enabled"] is True
    assert st["ticks_total"] == 2
    assert st["venues"] == ["binance", "bybit"]
    assert st["replaces_http_poll"] is False
    assert "BTCUSDT" in st["last_by_symbol"]
    # Primary policy: bybit must not overwrite fresh binance last
    assert st["last_by_symbol"]["BTCUSDT"]["price_i"] == 6_401_250_000_000
    assert st["last_by_symbol"]["BTCUSDT"]["venue"] == "binance"
    assert st["price_venue_policy"] == "primary"
    assert "ring_drops" in st


def test_primary_stale_allows_secondary() -> None:
    import time

    now = int(time.time() * 1000)
    feed = NativeWsFeed(
        venue="binance",
        venues=["binance", "bybit"],
        symbols=["ETHUSDT"],
        ring_size=32,
        stale_ms=1_000,
        price_venue_policy="primary",
    )
    feed.push_for_test(
        {
            "venue": "binance",
            "symbol": "ETHUSDT",
            "price_i": 100,
            "qty_i": 1,
            "ts_ms": now - 5_000,
        }
    )
    feed.push_for_test(
        {
            "venue": "bybit",
            "symbol": "ETHUSDT",
            "price_i": 200,
            "qty_i": 1,
            "ts_ms": now,
        }
    )
    assert feed.status()["last_by_symbol"]["ETHUSDT"]["price_i"] == 200


def test_suggest_symbols_from_sectors() -> None:
    from radar.ingest.tick_ring import suggest_symbols_from_sectors

    got = suggest_symbols_from_sectors(
        {
            "spot_alts": [
                {"symbol": "AAAUSDT"},
                {"symbol": "BBBUSDT"},
            ]
        },
        seed=["BTCUSDT"],
        n=3,
    )
    assert got[0] == "BTCUSDT"
    assert "AAAUSDT" in got
    assert len(got) <= 3


def test_ring_drops_when_full() -> None:
    feed = NativeWsFeed(
        venue="binance",
        symbols=["BTCUSDT"],
        ring_size=32,
        stale_ms=60_000,
    )
    for i in range(40):
        feed.push_for_test(
            {
                "venue": "binance",
                "symbol": "BTCUSDT",
                "price_i": 100 + i,
                "qty_i": 1,
                "ts_ms": 1_700_000_000_000 + i,
            }
        )
    assert feed.status()["ring_drops"] >= 8

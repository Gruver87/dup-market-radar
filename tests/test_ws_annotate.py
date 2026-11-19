"""Unit tests for additive WS row annotation (no live network)."""

from __future__ import annotations

from radar.ingest.ws_annotate import annotate_snapshot_rows


def test_annotate_fresh_row() -> None:
    snap = {
        "sectors": {
            "spot_alts": [
                {"symbol": "BTC-USDT", "price_i": 1, "venue": "kucoin"},
                {"symbol": "ETHUSDT", "price_i": 2, "venue": "binance"},
            ]
        }
    }
    overlay = {
        "last_by_symbol": {
            "BTCUSDT": {
                "price_i": 99,
                "qty_i": 10,
                "ts_ms": 1,
                "age_ms": 100,
                "venue": "binance",
                "side": "buy",
            }
        }
    }
    out, n = annotate_snapshot_rows(snap, overlay, max_age_ms=15_000)
    assert n == 1
    row = out["sectors"]["spot_alts"][0]
    assert row["price_i"] == 1  # SoT untouched
    assert row["ws_price_i"] == 99
    assert row["ws_replaces_price_i"] is False
    assert out["sectors"]["spot_alts"][1].get("ws_price_i") is None


def test_annotate_skips_stale() -> None:
    snap = {"sectors": {"spot_alts": [{"symbol": "BTCUSDT", "price_i": 1}]}}
    overlay = {
        "last_by_symbol": {
            "BTCUSDT": {"price_i": 99, "age_ms": 99_000, "venue": "binance"}
        }
    }
    out, n = annotate_snapshot_rows(snap, overlay, max_age_ms=15_000)
    assert n == 0
    assert "ws_price_i" not in out["sectors"]["spot_alts"][0]

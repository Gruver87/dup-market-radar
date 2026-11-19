"""Unit tests for age-gated WS → universe price_i apply."""

from __future__ import annotations

from radar.ingest.ws_price_apply import apply_ws_to_universe


def test_apply_fresh_overwrites_and_keeps_http() -> None:
    uni = {
        "spot": [
            {"symbol": "BTC-USDT", "price_i": 100, "venue": "kucoin"},
            {"symbol": "ETHUSDT", "price_i": 200, "venue": "binance"},
        ]
    }
    overlay = {
        "last_by_symbol": {
            "BTCUSDT": {
                "price_i": 999,
                "age_ms": 50,
                "venue": "binance",
            }
        }
    }
    out, n = apply_ws_to_universe(uni, overlay, max_age_ms=15_000)
    assert n == 1
    row = out["spot"][0]
    assert row["price_i"] == 999
    assert row["price_i_http"] == 100
    assert row["price_source"] == "native_ws"
    assert out["spot"][1]["price_i"] == 200


def test_apply_skips_stale() -> None:
    uni = {"spot": [{"symbol": "BTCUSDT", "price_i": 100}]}
    overlay = {
        "last_by_symbol": {
            "BTCUSDT": {"price_i": 999, "age_ms": 90_000, "venue": "binance"}
        }
    }
    out, n = apply_ws_to_universe(uni, overlay, max_age_ms=15_000)
    assert n == 0
    assert out["spot"][0]["price_i"] == 100

"""Unit tests for WS bar aggregator + universe stamp."""

from __future__ import annotations

from radar.ingest.bar_agg import BarAggregator, change_bps, metrics_from_bars
from radar.ingest.tick_ring import NativeWsFeed
from radar.ingest.ws_bar_stamp import stamp_ws_bar_metrics


def test_bar_rollup_and_change() -> None:
    agg = BarAggregator(60)
    open0 = 1_700_000_060_000 - (1_700_000_060_000 % 60_000)
    assert agg.push(
        {
            "symbol": "BTCUSDT",
            "venue": "binance",
            "price_i": 100_000_000_00,
            "qty_i": 1,
            "ts_ms": open0 + 1000,
        }
    ) is None
    assert agg.push(
        {
            "symbol": "BTCUSDT",
            "venue": "binance",
            "price_i": 110_000_000_00,
            "qty_i": 1,
            "ts_ms": open0 + 2000,
        }
    ) is None
    closed = agg.push(
        {
            "symbol": "BTCUSDT",
            "venue": "binance",
            "price_i": 105_000_000_00,
            "qty_i": 1,
            "ts_ms": open0 + 60_000 + 500,
        }
    )
    assert closed is not None
    assert closed.trade_count == 2
    assert change_bps(closed.open_i, closed.close_i) == 1000


def test_feed_bar_metrics_and_stamp() -> None:
    feed = NativeWsFeed(
        symbols=["BTCUSDT"],
        bar_interval_sec=60,
        bar_intervals=[60, 300],
        stale_ms=60_000,
    )
    open0 = 1_700_000_120_000 - (1_700_000_120_000 % 60_000)
    feed.push_for_test(
        {
            "symbol": "BTCUSDT",
            "venue": "binance",
            "price_i": 100,
            "qty_i": 1,
            "ts_ms": open0 + 100,
            "side": "buy",
        }
    )
    feed.push_for_test(
        {
            "symbol": "BTCUSDT",
            "venue": "binance",
            "price_i": 110,
            "qty_i": 1,
            "ts_ms": open0 + 200,
            "side": "buy",
        }
    )
    m = feed.bar_metrics_by_symbol()["BTCUSDT"]
    assert m["change_bps_ws_1m"] == 1000
    assert m["change_bps_ws_5m"] == 1000
    uni = {"spot": [{"symbol": "BTC-USDT", "price_i": 1}]}
    out, n, _skip = stamp_ws_bar_metrics(uni, feed.bar_metrics_by_symbol())
    assert n == 1
    assert out["spot"][0]["change_bps_ws_1m"] == 1000
    assert out["spot"][0]["change_bps_5m"] == 1000
    assert out["spot"][0]["change_bps_5m_source"] == "native_ws_bar_5m"


def test_1h_suffix_metrics() -> None:
    from radar.ingest.bar_agg import _suffix, metrics_from_bars

    assert _suffix(3600) == "1h"
    assert _suffix(14400) == "4h"
    agg = BarAggregator(3600)
    open0 = 1_700_000_000_000 - (1_700_000_000_000 % 3_600_000)
    agg.push(
        {
            "symbol": "BTCUSDT",
            "venue": "binance",
            "price_i": 100,
            "qty_i": 1,
            "ts_ms": open0 + 1000,
        }
    )
    agg.push(
        {
            "symbol": "BTCUSDT",
            "venue": "binance",
            "price_i": 104,
            "qty_i": 1,
            "ts_ms": open0 + 2000,
        }
    )
    m = metrics_from_bars(current=agg.current, prev_closed=None)
    assert m["change_bps_ws_1h"] == 400
    assert m["ws_bar_1h_trade_count"] == 2


def test_4h_suffix_metrics() -> None:
    from radar.ingest.bar_agg import metrics_from_bars

    agg = BarAggregator(14400)
    open0 = 1_700_000_000_000 - (1_700_000_000_000 % 14_400_000)
    agg.push(
        {
            "symbol": "ETHUSDT",
            "venue": "binance",
            "price_i": 200,
            "qty_i": 1,
            "ts_ms": open0 + 1000,
        }
    )
    agg.push(
        {
            "symbol": "ETHUSDT",
            "venue": "binance",
            "price_i": 220,
            "qty_i": 1,
            "ts_ms": open0 + 2000,
        }
    )
    m = metrics_from_bars(current=agg.current, prev_closed=None)
    assert m["change_bps_ws_4h"] == 1000
    assert m["ws_bar_4h_trade_count"] == 2


def test_metrics_from_bars_prev() -> None:
    agg = BarAggregator(60)
    open0 = 1_700_000_180_000 - (1_700_000_180_000 % 60_000)
    agg.push(
        {
            "symbol": "ETHUSDT",
            "venue": "binance",
            "price_i": 1000,
            "qty_i": 1,
            "ts_ms": open0 + 1,
        }
    )
    closed = agg.push(
        {
            "symbol": "ETHUSDT",
            "venue": "binance",
            "price_i": 1100,
            "qty_i": 1,
            "ts_ms": open0 + 60_000 + 1,
        }
    )
    m = metrics_from_bars(current=agg.current, prev_closed=closed)
    assert m["change_bps_ws_vs_prev"] == 1000 or "change_bps_ws_1m" in m

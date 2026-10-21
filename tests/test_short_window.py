"""Short-window 15m/1h candle helpers + spot_alts MTF blend (0.2.46)."""

from radar.ingest.normalize import enrich_market_row
from radar.ingest.short_window import (
    bps_to_pct_str,
    candle_change_bps,
    change_bps_from_closed_span,
    change_bps_from_closed_span_ohlc,
    closed_buckets,
    select_short_window_sample,
    vol_vs_median_bps_from_closed,
    pick_1h_bucket,
    pick_closed_bucket,
)
from radar.score.futures import score_futures
from radar.score.spot_alts import score_spot_alts


def test_candle_change_bps_up():
    assert candle_change_bps("100", "101") == 100


def test_candle_change_bps_down():
    assert candle_change_bps("100", "99") == -100


def test_bps_to_pct_str():
    assert bps_to_pct_str(150) == "1.50"
    assert bps_to_pct_str(-25) == "-0.25"


def test_pick_1h_bucket_prefers_closed():
    candles = [
        ["7000", "10", "11", "12", "9", "1", "1"],
        ["6000", "10", "10.5", "11", "9", "1", "1"],
    ]
    b = pick_1h_bucket(candles, now_sec=10_000)
    assert b is not None
    assert b[0] == "6000"
    assert b[2] == "10.5"


def test_pick_closed_15m():
    # period 900; start 8000 closed at now=10000 (8000+900=8900)
    candles = [
        ["9100", "10", "10.2", "11", "9", "1", "1"],  # open
        ["8000", "10", "10.1", "11", "9", "1", "1"],  # closed
    ]
    b = pick_closed_bucket(candles, period_sec=900, now_sec=10_000)
    assert b is not None
    assert b[0] == "8000"


def test_1h_from_four_15m_buckets():
    # four closed 15m: open 100 → close 104 = +400 bps
    closed = [
        ["1000", "100", "101", "102", "99", "1", "1"],
        ["1900", "101", "102", "103", "100", "1", "1"],
        ["2800", "102", "103", "104", "101", "1", "1"],
        ["3700", "103", "104", "105", "102", "1", "1"],
    ]
    assert change_bps_from_closed_span(closed, n_buckets=4) == 400
    raw = [
        ["1000", "100", "101", "102", "99", "1", "1"],
        ["1900", "101", "102", "103", "100", "1", "1"],
        ["2800", "102", "103", "104", "101", "1", "1"],
        ["3700", "103", "104", "105", "102", "1", "1"],
        ["9100", "104", "105", "106", "103", "1", "1"],  # still open at now=10000
    ]
    got = closed_buckets(raw, period_sec=900, now_sec=10_000)
    assert len(got) == 4
    assert got[-1][0] == "3700"


def test_4h_from_sixteen_15m_buckets():
    closed = []
    for i in range(16):
        start = 1000 + i * 900
        # open climbs 100 → … close of last = 116
        o = 100 + i
        c = 101 + i
        closed.append([str(start), str(o), str(c), str(c), str(o), "1", "1"])
    # open first 100, close last 116 → +1600 bps
    assert change_bps_from_closed_span(closed, n_buckets=16) == 1600
    assert change_bps_from_closed_span(closed, n_buckets=16) is not None
    assert change_bps_from_closed_span(closed[:15], n_buckets=16) is None


def test_kucoin_symbol_raw_helpers():
    from radar.ingest.short_window import (
        kucoin_futures_symbol_raw,
        kucoin_spot_symbol_raw,
    )

    assert kucoin_spot_symbol_raw({"symbol": "SOLUSDT"}) == "SOL-USDT"
    assert kucoin_spot_symbol_raw({"symbol_raw": "ETH-USDT"}) == "ETH-USDT"
    assert kucoin_futures_symbol_raw({"symbol": "BTCUSDT"}) == "XBTUSDTM"
    assert kucoin_futures_symbol_raw({"symbol": "ETHUSDT"}) == "ETHUSDTM"


def test_select_short_window_sample_mixes_vol_and_momentum():
    rows = [
        {"symbol": "AAAUSDT", "quote_volume": "100000000", "change_pct": "1.0"},
        {"symbol": "BBBUSDT", "quote_volume": "90000000", "change_pct": "2.0"},
        {"symbol": "CCCUSDT", "quote_volume": "80000000", "change_pct": "3.0"},
        {"symbol": "DDDUSDT", "quote_volume": "1000000", "change_pct": "50.0"},  # mom
        {"symbol": "EEEUSDT", "quote_volume": "2000000", "change_pct": "40.0"},
        {"symbol": "BTCUSDT", "quote_volume": "5000", "change_pct": "0.1"},  # anchor
    ]
    # top_n=4 → n_vol=2, n_mom=2 → AAA,BBB + DDD,EEE; BTC pinned if room
    sample = select_short_window_sample(rows, top_n=4)
    syms = [r["symbol"] for r in sample]
    assert "AAAUSDT" in syms and "BBBUSDT" in syms
    assert "DDDUSDT" in syms
    assert "BTCUSDT" in syms
    assert len(syms) <= 7


def test_futures_kline_window_is_milliseconds():
    """KuCoin futures /kline/query from/to are ms; spot candles use seconds."""
    now = 1_792_616_593
    lookback_sec = 18_000
    now_ms = now * 1000
    from_ms = now_ms - lookback_sec * 1000
    assert from_ms == 1_792_598_593_000
    assert from_ms > 10_000_000_000  # clearly ms, not unix seconds


def test_candle_start_sec_normalizes_ms():
    from radar.ingest.short_window import candle_start_sec, closed_buckets

    assert candle_start_sec(1_792_616_593) == 1_792_616_593
    assert candle_start_sec(1_792_616_593_000) == 1_792_616_593
    # futures-shaped closed bars with ms timestamps → still countable
    raw = [
        [str(1_000_000 + i * 900_000), "100", "101", "102", "99", "1"]
        for i in range(4)
    ]
    # convert: those are fake ms-like if > 1e10 — use real ms scale
    base_ms = 1_792_600_000_000
    raw = [
        [str(base_ms + i * 900_000), "100", "101", "102", "99", "1"]
        for i in range(4)
    ]
    got = closed_buckets(raw, period_sec=900, now_sec=1_792_603_700)
    assert len(got) == 4


def test_futures_ohlc_1h_span():
    # futures kline: [time, open, high, low, close, volume]
    closed = [
        ["1000", "100", "105", "99", "101", "1"],
        ["1900", "101", "106", "100", "102", "1"],
        ["2800", "102", "107", "101", "103", "1"],
        ["3700", "103", "108", "102", "104", "1"],
    ]
    assert (
        change_bps_from_closed_span_ohlc(
            closed, n_buckets=4, open_idx=1, close_idx=4
        )
        == 400
    )


def test_futures_mtf_bias_and_diverge():
    row = enrich_market_row(
        {
            "venue": "kucoin",
            "market": "futures",
            "symbol": "ETHUSDT",
            "price": "3000",
            "change_pct": "5.0",
            "change_pct_1h": "1.00",
            "change_pct_15m": "-0.50",
            "change_pct_4h": "-1.00",
            "quote_volume": "200000000",
            "funding_rate": "0.0001",
            "open_interest_usdt": "500000000",
        }
    )
    cfg = {
        "sectors": {
            "futures": {
                "extreme_funding_rate_i": 50000,
                "min_open_interest_usdt": 1000000,
            }
        }
    }
    out = score_futures({"futures": [row]}, cfg)
    assert len(out) == 1
    assert out[0]["setup"] == "short"  # |15m|>=30 → fade bias
    assert "mtf_diverge_15m" in out[0]["risk_flags"]
    assert out[0]["metrics"].get("change_bps_15m") == -50


def test_vol_vs_median_bps_surge():
    # eight 15m buckets → two 1h windows; recent 4× high amount vs prior median
    closed = []
    for i in range(8):
        start = 1000 + i * 900
        # amount index 6: prior windows ~100, recent ~300 → +20000 bps (3x)
        amt = "100" if i < 4 else "300"
        closed.append([str(start), "10", "10", "11", "9", "1", amt])
    assert vol_vs_median_bps_from_closed(closed, n_buckets=4) == 20_000


def test_vol_vs_median_bps_needs_two_windows():
    closed = [
        [str(1000 + i * 900), "10", "10", "11", "9", "1", "50"] for i in range(4)
    ]
    assert vol_vs_median_bps_from_closed(closed, n_buckets=4) is None


def test_spot_alts_15m_bias_and_diverge():
    row = enrich_market_row(
        {
            "venue": "kucoin",
            "market": "spot",
            "symbol": "SOLUSDT",
            "price": "150",
            "change_pct": "8.0",
            "change_pct_1h": "1.00",
            "change_pct_15m": "-0.40",
            "change_pct_4h": "-2.00",
            "quote_volume": "80000000",
            "bid": "149.9",
            "ask": "150.1",
            "high": "155",
            "low": "145",
            "vol_vs_median_bps": 8000,
        }
    )
    assert row.get("change_bps_15m") == -40
    assert row.get("change_bps_4h") == -200
    assert row.get("vol_vs_median_bps") == 8000
    cfg = {
        "stable_bases": ["USDT"],
        "meme_keywords": [],
        "sectors": {"spot_alts": {"min_quote_volume_usdt": 500000}},
    }
    out = score_spot_alts({"spot": [row]}, cfg)
    assert len(out) == 1
    assert "mtf_diverge_15m" in out[0]["risk_flags"]
    assert "mtf_diverge_4h" in out[0]["risk_flags"]
    assert "vol_surge_1h" in out[0]["risk_flags"]
    assert out[0]["metrics"].get("change_bps_15m") == -40
    assert out[0]["metrics"].get("change_bps_4h") == -200
    assert out[0]["metrics"].get("vol_vs_median_bps") == 8000
    assert out[0]["setup"] == "fade-watch"  # |15m|>=30 → short bias


def test_spot_alts_blends_1h_and_flags_diverge():
    row = enrich_market_row(
        {
            "venue": "kucoin",
            "market": "spot",
            "symbol": "SOLUSDT",
            "price": "150",
            "change_pct": "8.0",
            "change_pct_1h": "-1.00",
            "quote_volume": "80000000",
            "bid": "149.9",
            "ask": "150.1",
            "high": "155",
            "low": "145",
        }
    )
    assert row.get("change_bps_1h") == -100
    cfg = {
        "stable_bases": ["USDT"],
        "meme_keywords": [],
        "sectors": {"spot_alts": {"min_quote_volume_usdt": 500000}},
    }
    out = score_spot_alts({"spot": [row]}, cfg)
    assert len(out) == 1
    assert "mtf_diverge_1h" in out[0]["risk_flags"]
    assert out[0]["setup"] == "fade-watch"


def test_apply_ohlc_mtf_from_binance_style_buckets():
    from radar.ingest.short_window import _apply_ohlc_mtf

    # 4 closed 15m OHLC bars: open 100 → close 104 = +400 bps over 1h
    # Layout: [t, open, high, low, close, vol, …, quote]
    now = 10_000
    buckets = []
    for i in range(4):
        start = 1000 + i * 900
        o = 100 + i
        c = 101 + i
        buckets.append(
            [str(start), str(o), str(c), str(o), str(c), "1", "0", str(1000 + i)]
        )
    row: dict = {"symbol": "BTCUSDT"}
    counts = {"n_15m": 0, "n_1h": 0, "n_4h": 0, "n_vol_vs": 0}
    _apply_ohlc_mtf(row, buckets, now=now, counts=counts, quote_vol_idx=7)
    assert row.get("change_pct_15m") == "0.97"  # last open 103 → close 104
    assert row.get("change_pct_1h") == "4.00"  # open 100 → close 104
    assert counts["n_15m"] == 1
    assert counts["n_1h"] == 1


def test_binance_spot_attach_skips_bybit_venue():
    """Bybit-only symbols must not hit Binance klines (400); counted as no-op."""
    from radar.ingest.short_window import attach_binance_spot_short_windows

    class _BoomHttp:
        def get_json(self, *a, **k):
            raise AssertionError("binance must not be called for venue=bybit")

    rows = [
        {
            "venue": "bybit",
            "symbol": "BSBUSDT",
            "quote_volume": "2000000",
            "change_pct": "10",
        }
    ]
    counts = attach_binance_spot_short_windows(_BoomHttp(), rows, top_n=8)
    assert counts["n_15m"] == 0


def test_mexc_futures_contract_path_and_parallel_ohlc():
    from radar.ingest.short_window import (
        mexc_futures_contract_path,
        mexc_parallel_klines_to_ohlc,
        _apply_ohlc_mtf,
    )

    assert mexc_futures_contract_path({"symbol": "DOPEUSDT"}) == "DOPE_USDT"
    assert mexc_futures_contract_path({"symbol": "SILVERUSDT"}) == "SILVER_USDT"
    # 4×15m closed bars in lookback
    payload = {
        "time": [1000, 1900, 2800, 3700],
        "open": [100, 101, 102, 103],
        "high": [101, 102, 103, 104],
        "low": [99, 100, 101, 102],
        "close": [101, 102, 103, 104],
    }
    buckets = mexc_parallel_klines_to_ohlc(payload, cutoff_sec=500)
    assert len(buckets) == 4
    assert buckets[0][1] == 100 and buckets[-1][4] == 104
    row: dict = {"symbol": "DOPEUSDT", "venue": "mexc"}
    counts = {"n_15m": 0, "n_1h": 0, "n_4h": 0}
    _apply_ohlc_mtf(row, buckets, now=10_000, counts=counts)
    assert row.get("change_pct_1h") == "4.00"
    assert counts["n_15m"] == 1

"""Futures entry/exit watch zones (honesty: hints only, never futures auto)."""

from radar.score.futures import score_futures


def test_futures_long_short_exit_hints():
    universe = {
        "futures": [
            {
                "symbol": "AAAUSDT",
                "venue": "kucoin",
                "market": "futures",
                "price_i": 100_000_000,  # 1.0
                "change_bps": 200,
                "change_bps_1h": 100,
                "change_bps_15m": 50,
                "quote_volume_i": 5_000_000_000_000,
                "open_interest_i": 2_000_000_000_000,
                "funding_rate_i": 0,
            },
            {
                "symbol": "BBBUSDT",
                "venue": "kucoin",
                "market": "futures",
                "price_i": 200_000_000,  # 2.0
                "change_bps": -300,
                "change_bps_1h": -150,
                "change_bps_15m": -80,
                "quote_volume_i": 5_000_000_000_000,
                "open_interest_i": 2_000_000_000_000,
                "funding_rate_i": 0,
            },
        ]
    }
    rows = score_futures(universe, {"sectors": {"futures": {}}})
    by = {r["symbol"]: r for r in rows}
    assert by["AAAUSDT"]["setup"] == "long"
    assert "pullback" in by["AAAUSDT"]["entry_hint"]
    assert "TP watch" in by["AAAUSDT"]["exit_hint"]
    assert "invalidate <" in by["AAAUSDT"]["exit_hint"]
    assert by["BBBUSDT"]["setup"] == "short"
    assert "reject" in by["BBBUSDT"]["entry_hint"]
    assert "TP watch" in by["BBBUSDT"]["exit_hint"]
    assert "invalidate >" in by["BBBUSDT"]["exit_hint"]

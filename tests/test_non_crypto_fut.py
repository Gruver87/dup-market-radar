from radar.classify.sectors import is_non_crypto_futures_symbol
from radar.score.futures import score_futures


def test_non_crypto_bases_blocked():
    assert is_non_crypto_futures_symbol("XAUUSDT")
    assert is_non_crypto_futures_symbol("USOILUSDT")
    assert not is_non_crypto_futures_symbol("BTCUSDT")
    assert is_non_crypto_futures_symbol("FOOUSDT", extra_bases=["FOO"])


def test_score_futures_skips_xau():
    universe = {
        "futures": [
            {
                "symbol": "XAUUSDT",
                "price_i": 200_000_000_000,
                "change_bps": 100,
                "quote_volume_i": 10_000_000_000_000,
                "open_interest_i": 10_000_000_000_000,
                "funding_rate_i": 0,
                "venue": "kucoin",
                "market": "futures",
            },
            {
                "symbol": "BTCUSDT",
                "price_i": 60_000_000_000_000,
                "change_bps": 50,
                "quote_volume_i": 10_000_000_000_000,
                "open_interest_i": 10_000_000_000_000,
                "funding_rate_i": 0,
                "venue": "kucoin",
                "market": "futures",
            },
        ]
    }
    rows = score_futures(universe, {"sectors": {"futures": {}}})
    syms = [r["symbol"] for r in rows]
    assert "XAUUSDT" not in syms
    assert "BTCUSDT" in syms

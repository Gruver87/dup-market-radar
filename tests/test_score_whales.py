"""Whale sector — aggregate-first then min_notional gate."""

from radar.money import QUOTE_SCALE
from radar.score.whales import score_whales
from radar.ingest.kucoin import whale_symbols_kucoin


def _cfg(*, min_n: int = 10_000, dust: int = 100) -> dict:
    return {
        "sectors": {
            "whales": {
                "min_notional_usdt": min_n,
                "min_print_usdt": dust,
            }
        }
    }


def test_aggregate_then_threshold():
    # Ten 2k prints → 20k bucket ≥ 10k; per-print filter would drop all.
    prints = [
        {
            "venue": "kucoin",
            "symbol": "BTCUSDT",
            "side": "buy",
            "notional_i": 2_000 * QUOTE_SCALE,
            "price_i": 100_000_000_000,
            "market": "spot",
        }
        for _ in range(10)
    ]
    out = score_whales({"whales": prints, "spot": []}, _cfg())
    assert len(out) == 1
    assert out[0]["metrics"]["prints"] == 10
    assert out[0]["metrics"]["notional_i"] == 20_000 * QUOTE_SCALE


def test_bucket_below_min_dropped():
    prints = [
        {
            "venue": "kucoin",
            "symbol": "ETHUSDT",
            "side": "sell",
            "notional_i": 500 * QUOTE_SCALE,
            "price_i": 1,
            "market": "spot",
        }
        for _ in range(5)
    ]  # sum 2500 < 10000
    out = score_whales({"whales": prints, "spot": []}, _cfg())
    assert out == []


def test_dust_prints_skipped():
    prints = [
        {
            "venue": "kucoin",
            "symbol": "SOLUSDT",
            "side": "buy",
            "notional_i": 50 * QUOTE_SCALE,  # below dust 100
            "price_i": 1,
            "market": "spot",
        }
        for _ in range(200)
    ]
    out = score_whales({"whales": prints, "spot": []}, _cfg(min_n=1_000, dust=100))
    assert out == []


def test_whale_symbols_kucoin_mapping():
    assert whale_symbols_kucoin(["BTCUSDT", "eth-usdt", "SOLUSDT"]) == [
        "BTC-USDT",
        "ETH-USDT",
        "SOL-USDT",
    ]

from radar.ingest.dedupe import dedupe_by_symbol, dedupe_stats


def test_prefers_kucoin_over_binance():
    pri = {"kucoin": 0, "binance": 1, "bybit": 2}
    rows = [
        {
            "venue": "binance",
            "market": "spot",
            "symbol": "SOLUSDT",
            "price_i": 1,
            "quote_volume_i": 99,
        },
        {
            "venue": "kucoin",
            "market": "spot",
            "symbol": "SOLUSDT",
            "price_i": 2,
            "quote_volume_i": 1,
        },
        {
            "venue": "bybit",
            "market": "spot",
            "symbol": "ETHUSDT",
            "price_i": 3,
            "quote_volume_i": 5,
        },
    ]
    out = dedupe_by_symbol(rows, pri)
    by_sym = {r["symbol"]: r for r in out}
    assert by_sym["SOLUSDT"]["venue"] == "kucoin"
    assert by_sym["SOLUSDT"]["price_i"] == 2
    assert "ETHUSDT" in by_sym
    st = dedupe_stats(3, len(out))
    assert st["dropped"] == 1


def test_same_venue_keeps_higher_volume():
    pri = {"kucoin": 0}
    rows = [
        {"venue": "kucoin", "market": "spot", "symbol": "AAA", "quote_volume_i": 10},
        {"venue": "kucoin", "market": "spot", "symbol": "AAA", "quote_volume_i": 50},
    ]
    out = dedupe_by_symbol(rows, pri)
    assert len(out) == 1
    assert out[0]["quote_volume_i"] == 50

"""Per-scan kline soft-denylist."""

from radar.ingest.kline_skip import (
    init_kline_skip,
    is_client_refuse_exc,
    kline_skip_add,
    kline_skip_has,
)


class _Http:
    pass


def test_kline_skip_roundtrip():
    http = _Http()
    init_kline_skip(http)
    assert kline_skip_has(http, "binance_spot", "ILYUSDT") is False
    kline_skip_add(http, "binance_spot", "ILYUSDT")
    assert kline_skip_has(http, "binance_spot", "ILYUSDT") is True
    assert kline_skip_has(http, "binance_fut", "ILYUSDT") is False


def test_is_client_refuse_exc():
    assert is_client_refuse_exc(RuntimeError("400 Bad Request")) is True
    assert is_client_refuse_exc(RuntimeError("timeout")) is False


def test_patch_display_helpers():
    from radar.pipeline import (
        _display_missing_mtf_symbols,
        _patch_sectors_mtf_from_universe,
    )

    sectors = {
        "spot_alts": [
            {"symbol": "AAAUSDT", "metrics": {}},
            {"symbol": "BBBUSDT", "metrics": {"change_bps_15m": 10}},
        ],
        "futures": [{"symbol": "BTCUSDT", "metrics": {}}],
        "memes": [
            {
                "symbol": "PEPEUSDT",
                "venue": "kucoin",
                "metrics": {"source": "cex"},
            },
        ],
    }
    miss_s, miss_f = _display_missing_mtf_symbols(sectors, cap=8)
    assert "AAAUSDT" in miss_s
    assert "BBBUSDT" not in miss_s
    assert "PEPEUSDT" not in miss_s  # memes excluded from display ensure
    assert "BTCUSDT" in miss_f

    universe = {
        "spot": [
            {
                "symbol": "AAAUSDT",
                "change_bps_15m": 42,
                "change_bps_1h": 7,
            }
        ],
        "futures": [],
    }
    n = _patch_sectors_mtf_from_universe(sectors, universe)
    assert n >= 1
    assert sectors["spot_alts"][0]["metrics"]["change_bps_15m"] == 42

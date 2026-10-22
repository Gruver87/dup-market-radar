"""Process-lifetime kline denylist survives init_kline_skip (0.2.87+)."""

from radar.ingest.kline_skip import (
    init_kline_skip,
    kline_skip_add,
    kline_skip_bump,
    kline_skip_has,
)


class _H:
    pass


def test_persist_survives_scan_reset():
    http = _H()
    init_kline_skip(http)
    assert kline_skip_add(http, "binance_spot", "AKEUSDT") is True
    assert kline_skip_add(http, "binance_spot", "AKEUSDT") is False  # already known
    assert kline_skip_has(http, "binance_spot", "AKEUSDT")
    init_kline_skip(http)  # new scan
    assert kline_skip_has(http, "binance_spot", "AKEUSDT")
    assert not kline_skip_has(http, "binance_spot", "BTCUSDT")
    assert kline_skip_add(http, "binance_spot", "ILYUSDT") is True


def test_skip_bump_stats():
    http = _H()
    http.stats = {}
    init_kline_skip(http)
    kline_skip_bump(http)
    kline_skip_bump(http)
    assert http.stats["kline_skip_hits"] == 2


def test_init_creates_empty_scan_set():
    http = _H()
    init_kline_skip(http)
    assert hasattr(http, "_kline_skip")
    assert hasattr(http, "_kline_skip_persist")
    assert len(http._kline_skip) == 0

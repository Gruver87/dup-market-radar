"""Per-scan kline GET budget + MTF reuse helpers."""

from radar.ingest.kline_budget import budget_consume, budget_status, init_kline_budget
from radar.ingest.short_window import _missing_15m, _stamp_mtf, _target_fill_ratio


class _Http:
    pass


def test_budget_consume_respects_ceiling():
    http = _Http()
    init_kline_budget(http, 2)
    assert budget_consume(http) is True
    assert budget_consume(http) is True
    assert budget_consume(http) is False
    st = budget_status(http)
    assert st["kline_gets"] == 2
    assert st["kline_budget_left"] == 0


def test_budget_unlimited_when_none():
    http = _Http()
    init_kline_budget(http, None)
    for _ in range(5):
        assert budget_consume(http) is True
    assert budget_status(http)["kline_gets"] == 5
    assert budget_status(http)["kline_budget_left"] is None


def test_missing_15m_reuse_ttl():
    row: dict = {}
    assert _missing_15m(row) is True
    row["change_bps_15m"] = 10
    _stamp_mtf(row)
    assert _missing_15m(row, reuse_ttl_ms=0) is False
    assert _missing_15m(row, reuse_ttl_ms=60_000) is False
    # Force stale stamp
    row["mtf_attached_at_ms"] = int(row["mtf_attached_at_ms"]) - 120_000
    assert _missing_15m(row, reuse_ttl_ms=60_000) is True


def test_target_fill_ratio():
    rows = [
        {"change_bps_15m": 1},
        {"change_bps_15m": 2},
        {},
        {},
    ]
    assert abs(_target_fill_ratio(rows) - 0.5) < 1e-9
    assert _target_fill_ratio([]) == 1.0

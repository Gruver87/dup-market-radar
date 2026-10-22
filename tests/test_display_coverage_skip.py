"""Display ensure skips network when board MTF fill already >= 0.85 (0.2.83)."""

from radar.pipeline import _board_mtf_fill_ratio, _ensure_bucket_ok


def test_board_mtf_fill_ratio_counts_metrics():
    sectors = {
        "spot_alts": [
            {"symbol": "AUSDT", "metrics": {"change_bps_15m": 1}},
            {"symbol": "BUSDT", "metrics": {"change_bps_15m": 2}},
            {"symbol": "CUSDT", "metrics": {}},
        ]
    }
    ratio = _board_mtf_fill_ratio(sectors, "spot_alts", top_n=10)
    assert ratio is not None
    assert abs(ratio - (2 / 3)) < 1e-9
    assert not _ensure_bucket_ok(ratio)
    assert _ensure_bucket_ok(0.85)
    assert _ensure_bucket_ok(0.9)


def test_board_mtf_empty_is_none():
    assert _board_mtf_fill_ratio({"spot_alts": []}, "spot_alts", top_n=10) is None
    assert _ensure_bucket_ok(None) is True

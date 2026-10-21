from radar.pipeline import _ensure_bucket_ok, _ensure_fill_ratio


def test_empty_symbols_fill_is_none_not_one():
    assert _ensure_fill_ratio({"spot": []}, set(), "spot") is None
    assert _ensure_bucket_ok(None) is True
    assert _ensure_bucket_ok(0.9) is True
    assert _ensure_bucket_ok(0.5) is False


def test_partial_fill_ratio():
    uni = {
        "spot": [
            {"symbol": "AAAUSDT", "change_bps_15m": 10},
            {"symbol": "BBBUSDT"},
        ]
    }
    r = _ensure_fill_ratio(uni, {"AAAUSDT", "BBBUSDT"}, "spot")
    assert r == 0.5

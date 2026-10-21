from radar.ingest.parse import (
    abs_change_bps_sort_key,
    fraction_to_pct_str,
    keep_num,
    quote_volume_sort_key,
)
from radar.money import pct_to_bps, quote_i


def test_keep_num_no_float_path():
    assert keep_num("150.5") == "150.5"
    assert keep_num(150) == "150"
    assert keep_num(None) is None
    assert keep_num("") is None
    assert keep_num(True) is None


def test_keep_num_refuses_huge_string():
    assert keep_num("9" * 100) is None


def test_enrich_caps_symbol_length():
    from radar.ingest.normalize import enrich_market_row

    row = enrich_market_row(
        {
            "symbol": "a" * 80,
            "venue": "KuCoin-EXTRA-LONG-NAME",
            "setup": "x" * 200,
            "price": "1",
        }
    )
    assert len(row["symbol"]) == 32
    assert row["symbol"] == "A" * 32
    assert len(row["venue"]) <= 32
    assert len(row["setup"]) == 64


def test_fraction_to_pct():
    assert pct_to_bps(fraction_to_pct_str("0.05")) == 500
    assert pct_to_bps(fraction_to_pct_str("8.5")) == 850
    assert pct_to_bps(fraction_to_pct_str("-0.042")) == -420


def test_quote_volume_sort_key_int():
    a = {"quote_volume": "80000000"}
    b = {"quote_volume": "1000"}
    assert quote_volume_sort_key(a) > quote_volume_sort_key(b)
    assert quote_volume_sort_key(a) == quote_i("80000000")


def test_abs_change_bps_sort_key():
    hot = {"change_pct": "12.5"}
    cold = {"change_pct": "-1.0"}
    assert abs_change_bps_sort_key(hot) == 1250
    assert abs_change_bps_sort_key(hot) > abs_change_bps_sort_key(cold)
    assert abs_change_bps_sort_key({"change_bps": -400}) == 400

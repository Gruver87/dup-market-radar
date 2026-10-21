from radar.money import (
    BASE_SCALE,
    PRICE_SCALE,
    QUOTE_SCALE,
    apply_fee_bps,
    base_i,
    base_str,
    display_bps,
    display_score_centi,
    drop_bps,
    fraction_to_bps,
    mul_div,
    pct_to_bps,
    price_i,
    price_str,
    quote_i,
    quote_str,
    score_to_centi,
)


def test_price_quote_base_scales():
    assert price_i("150.5") == 15_050_000_000
    assert quote_i("10") == 10 * QUOTE_SCALE
    assert base_i("0.5") == BASE_SCALE // 2
    assert price_str(150 * PRICE_SCALE) == "150"
    assert quote_str(10_000_000) == "10"
    assert base_str(BASE_SCALE) == "1"


def test_price_i_refuses_huge_decimal_string():
    assert price_i("1" + "0" * 80) is None
    assert quote_i("9" * 100) is None


def test_pct_and_funding_bps():
    assert pct_to_bps("8.5") == 850
    assert pct_to_bps("-4.0") == -400
    assert fraction_to_bps("0.085") == 850
    assert display_bps(850) == "+8.50%"
    assert display_bps(-125) == "-1.25%"


def test_score_centi():
    assert score_to_centi("77.46") == 7746
    assert display_score_centi(7746) == "77.46"


def test_mul_div_and_fees_no_float():
    assert mul_div(10_000_000, PRICE_SCALE, 150 * PRICE_SCALE) == 10_000_000 // 150
    fee = apply_fee_bps(1_000_000, 30)
    assert fee == 997_000  # 0.30%
    assert drop_bps(100 * PRICE_SCALE, 97 * PRICE_SCALE) == 300  # 3% drop


def test_rejects_empty():
    assert price_i(None) is None
    assert quote_i("") is None


def test_notional_and_peg_from_ints():
    from radar.money import notional_i_from_price_qty, peg_deviation_bps_from_price

    # $60000 * 5 BTC → 300000 USDT micros
    assert notional_i_from_price_qty(60_000 * PRICE_SCALE, 5 * BASE_SCALE) == 300_000 * QUOTE_SCALE
    # $0.97 vs $1 → 300 bps
    assert peg_deviation_bps_from_price(97 * PRICE_SCALE // 100) == 300


def test_enrich_stable_peg_without_pct():
    from radar.ingest.normalize import enrich_market_row

    row = enrich_market_row(
        {"venue": "coingecko", "market": "stable", "symbol": "USDE", "price": "0.97"}
    )
    assert row["price_i"] == 97_000_000
    assert row["peg_deviation_bps"] == 300
    assert "price" not in row

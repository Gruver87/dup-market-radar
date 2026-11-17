from radar.money import PRICE_SCALE
from radar.telegram.price_alerts import (
    PriceAlertBook,
    evaluate_price_alert,
    parse_price_target,
)


def test_parse_abs_and_pct():
    base = 100 * PRICE_SCALE
    kind, target, direction = parse_price_target("105", baseline_price_i=base)
    assert kind == "abs"
    assert direction == "above"
    assert target == 105 * PRICE_SCALE

    kind2, target2, direction2 = parse_price_target("+5%", baseline_price_i=base)
    assert kind2 == "pct"
    assert direction2 == "above"
    assert target2 == 105 * PRICE_SCALE

    kind3, target3, direction3 = parse_price_target("-3%", baseline_price_i=base)
    assert kind3 == "pct"
    assert direction3 == "below"
    assert target3 == 97 * PRICE_SCALE


def test_evaluate_cross_and_cooldown(tmp_path):
    book = PriceAlertBook(tmp_path / "pa.json", max_alerts=10)
    base = 100 * PRICE_SCALE
    row = book.add(
        42,
        "BTCUSDT",
        target_price_i=105 * PRICE_SCALE,
        kind="abs",
        direction="above",
        baseline_price_i=base,
    )
    assert evaluate_price_alert(row, 104 * PRICE_SCALE) is None
    hit = evaluate_price_alert(row, 105 * PRICE_SCALE, cooldown_ms=0)
    assert hit is not None
    assert hit["type"] == "price_target"
    book.mark_fired(42, "BTCUSDT", 105 * PRICE_SCALE)
    refreshed = book.list_for_chat(42)[0]
    assert evaluate_price_alert(refreshed, 106 * PRICE_SCALE, cooldown_ms=300_000) is None


def test_digest_and_funding():
    from radar.telegram.digest import (
        collect_1h_movers,
        collect_extreme_funding,
        format_1h_digest_html,
        format_funding_html,
    )

    snap = {
        "ok": True,
        "sectors": {
            "spot_alts": [
                {
                    "symbol": "AAAUSDT",
                    "price_i": 1 * PRICE_SCALE,
                    "metrics": {"change_bps_1h": 500},
                },
                {
                    "symbol": "BBBUSDT",
                    "price_i": 2 * PRICE_SCALE,
                    "metrics": {"change_bps_1h": -800},
                },
            ],
            "futures": [
                {
                    "symbol": "AAAUSDT",
                    "price_i": 1 * PRICE_SCALE,
                    "metrics": {"funding_rate_i": 50_000},
                },
                {
                    "symbol": "CCCUSDT",
                    "price_i": 3 * PRICE_SCALE,
                    "metrics": {"funding_rate_i": -200_000},
                },
            ],
        },
    }
    movers = collect_1h_movers(snap, top_n=2)
    assert movers["gainers"][0]["symbol"] == "AAAUSDT"
    assert movers["losers"][0]["symbol"] == "BBBUSDT"
    html = format_1h_digest_html(movers, lang="en")
    assert "AAAUSDT" in html and "1h digest" in html

    fund = collect_extreme_funding(snap, top_n=2)
    assert fund[0]["symbol"] == "CCCUSDT"
    assert "Funding" in format_funding_html(fund, lang="en")

from decimal import Decimal

from radar.ingest.bybit import _bybit_change_pct_str, _norm
from radar.ingest.normalize import enrich_market_row
from radar.money import pct_to_bps


def test_bybit_fraction_to_percent_string():
    assert pct_to_bps(_bybit_change_pct_str("0.05")) == 500
    assert pct_to_bps(_bybit_change_pct_str("-0.042")) == -420
    assert Decimal(_bybit_change_pct_str("0.05")) == Decimal("5.00") or Decimal(
        _bybit_change_pct_str("0.05")
    ) == Decimal("5")
    # already percent-like
    assert pct_to_bps(_bybit_change_pct_str("8.5")) == 850


def test_bybit_spot_norm_enrich_bps():
    row = _norm(
        {
            "symbol": "SOLUSDT",
            "lastPrice": "150.5",
            "price24hPcnt": "0.085",
            "turnover24h": "80000000",
            "bid1Price": "150.4",
            "ask1Price": "150.6",
        },
        "spot",
    )
    enriched = enrich_market_row(row)
    assert enriched["change_bps"] == 850
    assert enriched["price_i"] == 15_050_000_000
    assert "change_pct" not in enriched

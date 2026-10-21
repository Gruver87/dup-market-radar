from radar.trade.auto_engine import AutoTradeEngine
from radar.trade.kucoin_manual import KuCoinManualTrader


def test_listings_soft_flag_allows_cross_venue():
    eng = AutoTradeEngine(KuCoinManualTrader(), lambda: {})
    sectors = {
        "listings": [
            {
                "symbol": "NEWUSDT",
                "setup": "new-listing-watch",
                "score_centi": 8000,
                "price_i": 100_000_000,
                "risk_flags": ["cross_venue_live"],
                "venue": "kucoin",
                "market": "spot",
            }
        ]
    }
    buys = eng._collect_buys(
        sectors,
        ["listings"],
        {"new-listing-watch"},
        5000,
        True,
        {},
        listings_soft_flags={"cross_venue_live"},
    )
    assert [b["symbol"] for b in buys] == ["NEWUSDT"]


def test_listings_hard_flag_still_blocks():
    eng = AutoTradeEngine(KuCoinManualTrader(), lambda: {})
    sectors = {
        "listings": [
            {
                "symbol": "BADUSDT",
                "setup": "new-listing-watch",
                "score_centi": 8000,
                "price_i": 100_000_000,
                "risk_flags": ["cross_venue_live", "no_live_volume"],
                "venue": "kucoin",
                "market": "spot",
            }
        ]
    }
    buys = eng._collect_buys(
        sectors,
        ["listings"],
        {"new-listing-watch"},
        5000,
        True,
        {},
        listings_soft_flags={"cross_venue_live"},
    )
    assert buys == []

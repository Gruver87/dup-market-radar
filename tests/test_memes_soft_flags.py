from radar.trade.auto_engine import AutoTradeEngine
from radar.trade.kucoin_manual import KuCoinManualTrader


def test_memes_soft_below_vol_allows():
    eng = AutoTradeEngine(KuCoinManualTrader(), lambda: {})
    sectors = {
        "memes": [
            {
                "symbol": "PEPEUSDT",
                "setup": "buy-watch",
                "score_centi": 8000,
                "price_i": 100_000_000,
                "risk_flags": ["below_vol_floor"],
                "venue": "kucoin",
                "market": "spot",
            }
        ]
    }
    buys = eng._collect_buys(
        sectors,
        ["memes"],
        {"buy-watch"},
        5000,
        True,
        {},
        soft_flags_by_sector={"memes": {"below_vol_floor"}},
    )
    assert [b["symbol"] for b in buys] == ["PEPEUSDT"]


def test_memes_other_flag_blocks():
    eng = AutoTradeEngine(KuCoinManualTrader(), lambda: {})
    sectors = {
        "memes": [
            {
                "symbol": "PEPEUSDT",
                "setup": "buy-watch",
                "score_centi": 8000,
                "price_i": 100_000_000,
                "risk_flags": ["below_vol_floor", "wide_spread_40bps"],
                "venue": "kucoin",
                "market": "spot",
            }
        ]
    }
    buys = eng._collect_buys(
        sectors,
        ["memes"],
        {"buy-watch"},
        5000,
        True,
        {},
        soft_flags_by_sector={"memes": {"below_vol_floor"}},
    )
    assert buys == []

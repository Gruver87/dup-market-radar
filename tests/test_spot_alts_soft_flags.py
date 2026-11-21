from radar.trade.auto_engine import AutoTradeEngine, _hard_risk_flags
from radar.trade.kucoin_manual import KuCoinManualTrader


def test_hard_risk_flags_prefix_star():
    soft = {"wide_range_24h", "wide_spread_*"}
    assert _hard_risk_flags({"wide_range_24h", "wide_spread_190bps"}, soft) == set()
    assert _hard_risk_flags({"wide_spread_190bps", "no_live_volume"}, soft) == {
        "no_live_volume"
    }


def test_spot_alts_soft_flag_allows_wide_range():
    eng = AutoTradeEngine(KuCoinManualTrader(), lambda: {})
    sectors = {
        "spot_alts": [
            {
                "symbol": "PIEVERSEUSDT",
                "setup": "buy-watch",
                "score_centi": 4300,
                "price_i": 100_000_000,
                "risk_flags": ["wide_range_24h", "wide_spread_190bps"],
                "venue": "kucoin",
                "market": "spot",
            }
        ]
    }
    buys = eng._collect_buys(
        sectors,
        ["spot_alts"],
        {"buy-watch", "long"},
        3500,
        True,
        {},
        soft_flags_by_sector={
            "spot_alts": {
                "wide_range_24h",
                "wide_spread_*",
                "mtf_diverge_15m",
            }
        },
        exec_venues={"kucoin"},
    )
    assert [b["symbol"] for b in buys] == ["PIEVERSEUSDT"]


def test_exec_venues_skips_non_kucoin():
    eng = AutoTradeEngine(KuCoinManualTrader(), lambda: {})
    sectors = {
        "spot_alts": [
            {
                "symbol": "AAAUSDT",
                "setup": "buy-watch",
                "score_centi": 9000,
                "price_i": 100_000_000,
                "risk_flags": [],
                "venue": "binance",
                "market": "spot",
            }
        ]
    }
    buys = eng._collect_buys(
        sectors,
        ["spot_alts"],
        {"buy-watch"},
        3500,
        True,
        {},
        soft_flags_by_sector={"spot_alts": set()},
        exec_venues={"kucoin"},
    )
    assert buys == []


def test_long_setup_eligible():
    eng = AutoTradeEngine(KuCoinManualTrader(), lambda: {})
    sectors = {
        "spot_alts": [
            {
                "symbol": "LONGUSDT",
                "setup": "long",
                "score_centi": 4000,
                "price_i": 100_000_000,
                "risk_flags": [],
                "venue": "kucoin",
                "market": "spot",
            }
        ]
    }
    buys = eng._collect_buys(
        sectors,
        ["spot_alts"],
        {"buy-watch", "long"},
        3500,
        True,
        {},
        soft_flags_by_sector={"spot_alts": set()},
        exec_venues={"kucoin"},
    )
    assert [b["symbol"] for b in buys] == ["LONGUSDT"]

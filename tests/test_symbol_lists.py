from radar.trade.auto_engine import AutoTradeEngine
from radar.trade.kucoin_manual import KuCoinManualTrader


def _row(sym: str, score: int = 8000):
    return {
        "symbol": sym,
        "setup": "buy-watch",
        "score_centi": score,
        "price_i": 100_000_000,
        "risk_flags": [],
        "venue": "kucoin",
        "market": "spot",
    }


def test_blacklist_blocks_buy():
    eng = AutoTradeEngine(KuCoinManualTrader(), lambda: {})
    sectors = {"spot_alts": [_row("AAAUSDT"), _row("BBBUSDT", 9000)]}
    buys = eng._collect_buys(
        sectors,
        ["spot_alts"],
        {"buy-watch"},
        5000,
        True,
        {},
        symbol_blacklist={"AAAUSDT"},
    )
    assert [b["symbol"] for b in buys] == ["BBBUSDT"]


def test_whitelist_only():
    eng = AutoTradeEngine(KuCoinManualTrader(), lambda: {})
    sectors = {"spot_alts": [_row("AAAUSDT"), _row("BBBUSDT", 9000)]}
    buys = eng._collect_buys(
        sectors,
        ["spot_alts"],
        {"buy-watch"},
        5000,
        True,
        {},
        symbol_whitelist={"AAAUSDT"},
    )
    assert [b["symbol"] for b in buys] == ["AAAUSDT"]

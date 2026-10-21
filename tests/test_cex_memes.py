from radar.score.memes import score_memes
from radar.trade.auto_engine import AutoTradeEngine
from radar.trade.kucoin_manual import KuCoinManualTrader


def test_cex_meme_scored_and_auto_eligible():
    universe = {
        "memes": [],
        "spot": [
            {
                "symbol": "PEPEUSDT",
                "venue": "kucoin",
                "market": "spot",
                "price_i": 1_000_000,
                "change_bps": 800,
                "change_bps_15m": 100,
                "change_bps_1h": 200,
                "quote_volume_i": 5_000_000_000_000,
            }
        ],
    }
    cfg = {
        "meme_keywords": ["PEPE"],
        "stable_bases": ["USDT"],
        "sectors": {
            "memes": {
                "include_cex_spot": True,
                "min_cex_quote_volume_usdt": 200_000,
            }
        },
    }
    rows = score_memes(universe, cfg)
    assert any(r["symbol"] == "PEPEUSDT" for r in rows)
    pepe = next(r for r in rows if r["symbol"] == "PEPEUSDT")
    assert pepe.get("venue") == "kucoin"
    assert (pepe.get("metrics") or {}).get("source") == "cex"

    eng = AutoTradeEngine(KuCoinManualTrader(), lambda: {})
    buys = eng._collect_buys(
        {"memes": rows},
        ["memes"],
        {"buy-watch", "fade-watch"},
        1000,
        False,
        {},
    )
    assert any(b["symbol"] == "PEPEUSDT" for b in buys)


def test_dex_meme_still_skipped_by_auto():
    eng = AutoTradeEngine(KuCoinManualTrader(), lambda: {})
    sectors = {
        "memes": [
            {
                "symbol": "XYZUSDT",
                "setup": "buy-watch",
                "score_centi": 9000,
                "price_i": 100_000_000,
                "risk_flags": [],
                "venue": "dexscreener",
                "market": "spot",
            }
        ]
    }
    buys = eng._collect_buys(
        sectors, ["memes"], {"buy-watch"}, 1000, False, {}
    )
    assert buys == []

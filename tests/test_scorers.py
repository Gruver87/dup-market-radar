from __future__ import annotations

from radar.config import load_config
from radar.ingest.normalize import enrich_list
from radar.rank.topn import rank_top
from radar.score.futures import score_futures
from radar.score.listings import score_listings
from radar.score.memes import score_memes
from radar.score.spot_alts import score_spot_alts
from radar.score.stables import score_stables
from radar.score.whales import score_whales


def _cfg():
    return load_config()


def test_spot_alts_ranks_momentum_and_excludes_stables_memes():
    universe = {
        "spot": enrich_list(
            [
                {
                    "venue": "binance",
                    "market": "spot",
                    "symbol": "SOLUSDT",
                    "price": "150.0",
                    "change_pct": "8.5",
                    "quote_volume": "80000000",
                    "bid": "149.9",
                    "ask": "150.1",
                },
                {
                    "venue": "binance",
                    "market": "spot",
                    "symbol": "USDTUSDC",
                    "price": "1.0",
                    "change_pct": "0.01",
                    "quote_volume": "90000000",
                },
                {
                    "venue": "binance",
                    "market": "spot",
                    "symbol": "PEPEUSDT",
                    "price": "0.00001",
                    "change_pct": "20.0",
                    "quote_volume": "50000000",
                },
                {
                    "venue": "binance",
                    "market": "spot",
                    "symbol": "AAAUSDT",
                    "price": "2.0",
                    "change_pct": "1.0",
                    "quote_volume": "1000",
                },
            ]
        )
    }
    rows = score_spot_alts(universe, _cfg())
    syms = {r["symbol"] for r in rows}
    assert "SOLUSDT" in syms
    assert "PEPEUSDT" not in syms
    assert "USDTUSDC" not in syms
    top = rank_top(rows, 10)
    assert top[0]["symbol"] == "SOLUSDT"
    assert top[0]["setup"] in {"buy-watch", "fade-watch"}
    assert isinstance(top[0]["score_centi"], int)
    assert isinstance(top[0]["price_i"], int)
    assert isinstance(top[0]["change_bps"], int)


def test_futures_extreme_funding_flips_side():
    universe = {
        "futures": enrich_list(
            [
                {
                    "venue": "binance",
                    "market": "futures",
                    "symbol": "ETHUSDT",
                    "price": "3000.0",
                    "change_pct": "5.0",
                    "quote_volume": "200000000",
                    "funding_rate": "0.002",
                    "open_interest_usdt": "500000000",
                },
                {
                    "venue": "binance",
                    "market": "futures",
                    "symbol": "XRPUSDT",
                    "price": "0.6",
                    "change_pct": "-4.0",
                    "quote_volume": "100000000",
                    "funding_rate": "-0.002",
                    "open_interest_usdt": "200000000",
                },
            ]
        )
    }
    rows = {r["symbol"]: r for r in score_futures(universe, _cfg())}
    assert rows["ETHUSDT"]["setup"] == "short"
    assert "extreme_positive_funding" in rows["ETHUSDT"]["risk_flags"]
    assert rows["XRPUSDT"]["setup"] == "long"
    assert isinstance(rows["ETHUSDT"]["score_centi"], int)


def test_stables_depeg_scores_higher():
    universe = {
        "stables": enrich_list(
            [
                {
                    "venue": "coingecko",
                    "symbol": "USDC",
                    "price": "1.0",
                    "peg_deviation_pct": "0.01",
                    "quote_volume": "1000000000",
                },
                {
                    "venue": "coingecko",
                    "symbol": "USDE",
                    "price": "0.97",
                    "peg_deviation_pct": "3.0",
                    "quote_volume": "100000000",
                },
            ]
        )
    }
    top = rank_top(score_stables(universe, _cfg()), 10)
    assert top[0]["symbol"] == "USDE"
    assert "depeg_warn" in top[0]["risk_flags"]
    assert isinstance(top[0]["score_centi"], int)


def test_memes_requires_liquidity():
    universe = {
        "memes": enrich_list(
            [
                {
                    "venue": "dexscreener",
                    "symbol": "WIF/SOL",
                    "price": "1.2",
                    "change_pct": "10",
                    "liquidity_usd": "200000",
                    "quote_volume": "500000",
                    "pair_created_at_ms": 1_700_000_000_000,
                    "chain": "solana",
                },
                {
                    "venue": "dexscreener",
                    "symbol": "RUG/SOL",
                    "price": "0.01",
                    "change_pct": "90",
                    "liquidity_usd": "100",
                    "quote_volume": "10",
                    "chain": "solana",
                },
            ]
        )
    }
    rows = score_memes(universe, _cfg())
    assert all(r["symbol"] != "RUG/SOL" for r in rows)
    assert any(r["symbol"] == "WIF/SOL" for r in rows)


def test_memes_dex_mtf_5m_bias_and_diverge():
    universe = {
        "memes": enrich_list(
            [
                {
                    "venue": "dexscreener",
                    "symbol": "BONK/SOL",
                    "price": "0.02",
                    "change_pct": "8.0",
                    "change_pct_1h": "1.0",
                    "change_pct_5m": "-0.40",
                    "liquidity_usd": "500000",
                    "quote_volume": "2000000",
                    "pair_created_at_ms": 1_700_000_000_000,
                    "chain": "solana",
                }
            ]
        )
    }
    rows = score_memes(universe, _cfg())
    assert len(rows) == 1
    assert rows[0]["setup"] == "fade-watch"
    assert "mtf_diverge_5m" in rows[0]["risk_flags"]
    assert rows[0]["metrics"].get("change_bps_5m") == -40


def test_listings_lookback():
    import time

    now = int(time.time() * 1000)
    universe = {
        "listings": [
            {
                "venue": "bybit",
                "symbol": "NEWUSDT",
                "listed_at_ms": now - 3_600_000,
                "market": "spot",
            },
            {
                "venue": "bybit",
                "symbol": "OLDUSDT",
                "listed_at_ms": now - 10 * 24 * 3_600_000,
                "market": "spot",
            },
        ],
        "spot": enrich_list(
            [
                {
                    "venue": "bybit",
                    "symbol": "NEWUSDT",
                    "price": "1.5",
                    "change_pct": "12.0",
                    "quote_volume": "8000000",
                }
            ]
        ),
    }
    rows = score_listings(universe, _cfg())
    syms = {r["symbol"] for r in rows}
    assert "NEWUSDT" in syms
    assert "OLDUSDT" not in syms


def test_listings_skips_ghost_and_joins_cross_venue():
    import time

    now = int(time.time() * 1000)
    universe = {
        "listings": [
            {
                "venue": "kucoin",
                "symbol": "GHOSTUSDT",
                "listed_at_ms": now - 1_000_000,
                "market": "spot",
            },
            {
                "venue": "kucoin",
                "symbol": "XVENUEUSDT",
                "listed_at_ms": now - 2_000_000,
                "market": "spot",
            },
            {
                "venue": "kucoin",
                "symbol": "FUTNEWUSDT",
                "listed_at_ms": now - 1_500_000,
                "market": "futures",
            },
        ],
        "spot": enrich_list(
            [
                {
                    "venue": "binance",
                    "symbol": "XVENUEUSDT",
                    "price": "2",
                    "change_pct": "5.0",
                    "quote_volume": "9000000",
                }
            ]
        ),
        "futures": enrich_list(
            [
                {
                    "venue": "kucoin",
                    "market": "futures",
                    "symbol": "FUTNEWUSDT",
                    "price": "3",
                    "change_pct": "8.0",
                    "quote_volume": "4000000",
                    "open_interest": "1000000",
                }
            ]
        ),
    }
    rows = score_listings(universe, _cfg())
    syms = {r["symbol"] for r in rows}
    assert "GHOSTUSDT" not in syms
    assert "XVENUEUSDT" in syms
    xv = next(r for r in rows if r["symbol"] == "XVENUEUSDT")
    assert "cross_venue_live" in xv["risk_flags"]
    assert xv["metrics"].get("live_venue") == "binance"
    assert "FUTNEWUSDT" in syms


def test_whales_spot_mtf_align_flag():
    universe = {
        "whales": enrich_list(
            [
                {
                    "venue": "kucoin",
                    "symbol": "BTCUSDT",
                    "price": "60000",
                    "qty": "5",
                    "notional_usdt": "200000",
                    "side": "buy",
                }
            ]
        ),
        "spot": enrich_list(
            [
                {
                    "venue": "kucoin",
                    "symbol": "BTCUSDT",
                    "price": "60000",
                    "change_pct": "2.0",
                    "change_pct_15m": "0.50",
                    "quote_volume": "100000000",
                }
            ]
        ),
    }
    rows = score_whales(universe, _cfg())
    assert len(rows) == 1
    assert "spot_mtf_align" in rows[0]["risk_flags"]
    assert rows[0]["metrics"].get("spot_bias_bps") == 50


def test_whales_aggregate_above_threshold():
    universe = {
        "whales": enrich_list(
            [
                {
                    "venue": "binance",
                    "symbol": "BTCUSDT",
                    "price": "60000",
                    "qty": "5",
                    "notional_usdt": "150000",
                    "side": "buy",
                },
                {
                    "venue": "binance",
                    "symbol": "BTCUSDT",
                    "price": "60000",
                    "qty": "5",
                    "notional_usdt": "150000",
                    "side": "buy",
                },
                {
                    "venue": "binance",
                    "symbol": "BTCUSDT",
                    "price": "60000",
                    "qty": "0.1",
                    "notional_usdt": "6000",
                    "side": "sell",
                },
            ]
        )
    }
    rows = score_whales(universe, _cfg())
    assert len(rows) == 1
    assert rows[0]["metrics"]["prints"] == 2
    assert rows[0]["setup"] == "whale-buy"
    assert isinstance(rows[0]["score_centi"], int)


def test_rank_top_unique():
    cands = [
        {"symbol": "A", "venue": "x", "setup": "long", "score_centi": 9000},
        {"symbol": "A", "venue": "x", "setup": "long", "score_centi": 8000},
        {"symbol": "B", "venue": "x", "setup": "short", "score_centi": 7000},
    ]
    top = rank_top(cands, 10)
    assert len(top) == 2
    assert top[0]["score_centi"] == 9000

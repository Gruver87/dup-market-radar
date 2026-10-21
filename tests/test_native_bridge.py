from radar.ingest.normalize import enrich_list
from radar.native_bridge import native_available, native_status, score_universe_native
from radar.score.engine import score_sectors


def test_native_status_shape():
    st = native_status()
    assert "available" in st
    assert st["backend"] in {"radar_native", "python"}


def test_engine_python_fallback_when_prefer_false():
    universe = {
        "spot": enrich_list(
            [
                {
                    "venue": "kucoin",
                    "market": "spot",
                    "symbol": "SOLUSDT",
                    "price": "150.0",
                    "change_pct": "5.0",
                    "quote_volume": "50000000",
                    "bid": "149.9",
                    "ask": "150.1",
                }
            ]
        ),
        "futures": [],
        "memes": [],
        "stables": [],
        "listings": [],
        "whales": [],
    }
    cfg = {
        "native": {"prefer": False},
        "stable_bases": ["USDT", "USDC"],
        "meme_keywords": ["PEPE"],
        "sectors": {
            "spot_alts": {"min_quote_volume_usdt": 500000, "min_price": "0.000001"},
            "futures": {},
            "memes": {},
            "stables": {},
            "listings": {},
            "whales": {},
        },
    }
    sectors, backend = score_sectors(universe, cfg, 10)
    assert backend == "python"
    assert any(r["symbol"] == "SOLUSDT" for r in sectors["spot_alts"])
    sol = next(r for r in sectors["spot_alts"] if r["symbol"] == "SOLUSDT")
    assert isinstance(sol["score_centi"], int)
    assert isinstance(sol["price_i"], int)


def test_native_integer_path_when_built():
    if not native_available():
        return
    universe = {
        "spot": enrich_list(
            [
                {
                    "venue": "kucoin",
                    "market": "spot",
                    "symbol": "SOLUSDT",
                    "price": "150.0",
                    "change_pct": "8.5",
                    "quote_volume": "80000000",
                    "bid": "149.9",
                    "ask": "150.1",
                },
                {
                    "venue": "kucoin",
                    "market": "spot",
                    "symbol": "PEPEUSDT",
                    "price": "0.00001",
                    "change_pct": "20.0",
                    "quote_volume": "50000000",
                },
            ]
        ),
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
                }
            ]
        ),
        "memes": [],
        "stables": [],
        "listings": [],
        "whales": [],
    }
    cfg = {
        "stable_bases": ["USDT", "USDC"],
        "meme_keywords": ["PEPE"],
        "sectors": {
            "spot_alts": {"min_quote_volume_usdt": 500000, "min_price": "0.000001"},
            "futures": {"extreme_funding_rate_i": 50000},
            "memes": {},
            "stables": {},
            "listings": {},
            "whales": {},
        },
    }
    sectors, backend = score_universe_native(universe, cfg, 10)
    assert backend == "radar_native"
    assert any(r["symbol"] == "SOLUSDT" for r in sectors["spot_alts"])
    assert all(r["symbol"] != "PEPEUSDT" for r in sectors["spot_alts"])
    sol = next(r for r in sectors["spot_alts"] if r["symbol"] == "SOLUSDT")
    assert isinstance(sol["score_centi"], int)
    assert isinstance(sol["price_i"], int)
    assert "score" not in sol or not isinstance(sol.get("score"), float)
    eth = next(r for r in sectors["futures"] if r["symbol"] == "ETHUSDT")
    assert eth["setup"] == "short"
    assert isinstance(eth["score_centi"], int)

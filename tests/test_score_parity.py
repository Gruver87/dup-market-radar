"""Python ↔ Rust score parity on a fixed integer fixture."""

from __future__ import annotations

from radar.ingest.normalize import enrich_list
from radar.native_bridge import native_available, score_universe_native
from radar.score.engine import score_sectors


def _fixture():
    return {
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
                    "symbol": "AAAUSDT",
                    "price": "2.0",
                    "change_pct": "-3.0",
                    "quote_volume": "2000000",
                    "bid": "1.99",
                    "ask": "2.01",
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
        "stables": enrich_list(
            [
                {
                    "venue": "coingecko",
                    "market": "stable",
                    "symbol": "USDE",
                    "price": "0.97",
                    "quote_volume": "100000000",
                }
            ]
        ),
        "listings": [],
        "whales": [],
    }


def _cfg():
    return {
        "stable_bases": ["USDT", "USDC"],
        "meme_keywords": ["PEPE"],
        "sectors": {
            "spot_alts": {"min_quote_volume_usdt": 500000, "min_price": "0.000001"},
            "futures": {"extreme_funding_rate_i": 50000, "min_open_interest_usdt": 1000000},
            "memes": {},
            "stables": {"depeg_warn_bps": 30},
            "listings": {},
            "whales": {},
        },
    }


def _index(rows):
    return {(r.get("venue"), r.get("symbol"), r.get("setup")): r for r in rows}


def test_python_rust_score_centi_parity():
    if not native_available():
        return
    universe = _fixture()
    cfg = _cfg()
    py_sectors, py_backend = score_sectors(universe, {**cfg, "native": {"prefer": False}}, 10)
    assert py_backend == "python"
    rust_sectors, rust_backend = score_universe_native(universe, cfg, 10)
    assert rust_backend == "radar_native"

    for sector in ("spot_alts", "futures", "stables"):
        py = _index(py_sectors.get(sector) or [])
        rs = _index(rust_sectors.get(sector) or [])
        assert set(py.keys()) == set(rs.keys()), f"{sector} key mismatch {py.keys()} vs {rs.keys()}"
        for k in py:
            assert py[k]["score_centi"] == rs[k]["score_centi"], (
                f"{sector} {k}: py={py[k]['score_centi']} rust={rs[k]['score_centi']}"
            )
            assert py[k]["setup"] == rs[k]["setup"]

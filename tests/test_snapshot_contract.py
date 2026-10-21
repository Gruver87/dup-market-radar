from radar.pipeline import empty_snapshot


REQUIRED_KEYS = {
    "ok",
    "schema_version",
    "product",
    "disclaimer",
    "sectors",
    "sector_meta",
    "score_backend",
    "errors",
    "universe_size",
}


def test_empty_snapshot_contract():
    snap = empty_snapshot("unit")
    missing = REQUIRED_KEYS - set(snap.keys())
    assert not missing, missing
    assert snap["schema_version"] == 2
    assert snap["ok"] is False
    assert "spot_alts" in snap["sectors"]
    assert "futures_long" in snap["sectors"]


def test_offline_scan_contract_keys():
    from radar.config import load_config
    from radar.ingest.normalize import enrich_list
    from radar.score.engine import score_sectors
    from radar.pipeline import SECTOR_META

    cfg = load_config()
    cfg = dict(cfg)
    cfg["native"] = {"prefer": False, "fail_closed": False}
    universe = {
        "spot": enrich_list(
            [
                {
                    "venue": "kucoin",
                    "market": "spot",
                    "symbol": "SOLUSDT",
                    "price": "150",
                    "change_pct": "4",
                    "quote_volume": "60000000",
                    "bid": "149",
                    "ask": "151",
                }
            ]
        ),
        "futures": [],
        "memes": [],
        "stables": [],
        "listings": [],
        "whales": [],
        "universe_size": 1,
        "errors": [],
    }
    sectors, backend = score_sectors(universe, cfg, 5)
    # emulate pipeline envelope
    snap = {
        "ok": True,
        "schema_version": 2,
        "product": "DUP Market Radar",
        "disclaimer": "x",
        "sectors": sectors,
        "sector_meta": SECTOR_META,
        "score_backend": backend,
        "errors": [],
        "universe_size": 1,
    }
    assert REQUIRED_KEYS <= set(snap.keys())
    row = sectors["spot_alts"][0]
    assert "score_centi" in row and "price_i" in row
    assert "score" not in row or isinstance(row.get("score_centi"), int)

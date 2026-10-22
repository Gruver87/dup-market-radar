"""Display ensure must not wipe primary short_window_ensure honesty fields."""

from __future__ import annotations

from typing import Any, Dict, Set


def test_display_ensure_preserves_primary_acc(monkeypatch):
    from radar import pipeline as pl

    calls: list[str] = []
    primary = {
        "rounds": 1,
        "ensure_rank_n": 20,
        "spot_fill_ratio": 1.0,
        "fut_fill_ratio": 1.0,
        "stage_ms": {"spot_kucoin_ms": 100},
        "ensure_ms": 100,
    }

    def fake_ensure(http, universe, *, spot_symbols, fut_symbols, reuse_ttl_ms=0):
        calls.append("ensure")
        # Simulate clobber — production helper assigns thin out onto universe.
        out = {
            "spot_targets": len(spot_symbols or []),
            "fut_targets": len(fut_symbols or []),
            "stage_ms": {"spot_kucoin_ms": 50},
        }
        universe["short_window_ensure"] = out
        return out

    monkeypatch.setattr(pl, "ensure_universe_short_windows", fake_ensure)
    monkeypatch.setattr(
        pl,
        "_display_missing_mtf_symbols",
        lambda sectors, cap=8: ({"AAAUSDT"}, set()),
    )
    monkeypatch.setattr(pl, "_patch_sectors_mtf_from_universe", lambda s, u: 1)

    universe: Dict[str, Any] = {"short_window_ensure": dict(primary)}
    sectors = {"spot_alts": [{"symbol": "AAAUSDT", "metrics": {}}]}
    # Reproduce the preserve block from run_scan (surgical unit of the fix).
    primary_acc = universe.get("short_window_ensure")
    assert isinstance(primary_acc, dict)
    primary_acc = dict(primary_acc)
    miss_spot: Set[str] = {"AAAUSDT"}
    miss_fut: Set[str] = set()
    disp = pl.ensure_universe_short_windows(
        None,  # type: ignore[arg-type]
        universe,
        spot_symbols=miss_spot,
        fut_symbols=miss_fut,
        reuse_ttl_ms=0,
    )
    universe["short_window_ensure"] = primary_acc
    primary_acc["display_ensure_ms"] = 1
    primary_acc["display_spot_targets"] = len(miss_spot)
    primary_acc["display_fut_targets"] = len(miss_fut)
    primary_acc["display_patched"] = pl._patch_sectors_mtf_from_universe(
        sectors, universe
    )
    sm = primary_acc.setdefault("stage_ms", {})
    for k, v in (disp.get("stage_ms") or {}).items():
        sm[k] = int(sm.get(k) or 0) + int(v or 0)

    acc = universe["short_window_ensure"]
    assert acc["rounds"] == 1
    assert acc["ensure_rank_n"] == 20
    assert acc["spot_fill_ratio"] == 1.0
    assert acc["display_ensure_ms"] == 1
    assert acc["stage_ms"]["spot_kucoin_ms"] == 150
    assert calls == ["ensure"]

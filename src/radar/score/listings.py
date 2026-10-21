from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from radar.money import QUOTE_SCALE, SCORE_CENTI, mul_div
from radar.score.common import blend_mtf_momentum, candidate, clamp_i


def _opt_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    return int(v)


def _index_live(
    rows: List[Dict[str, Any]],
) -> Tuple[Dict[tuple, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """(venue, symbol) index + symbol-only (prefer higher quote_volume_i)."""
    by_vs: Dict[tuple, Dict[str, Any]] = {}
    by_sym: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        sym = r.get("symbol")
        if not sym:
            continue
        venue = r.get("venue")
        by_vs[(venue, sym)] = r
        prev = by_sym.get(str(sym))
        vol = int(r.get("quote_volume_i") or 0)
        if prev is None or vol >= int(prev.get("quote_volume_i") or 0):
            by_sym[str(sym)] = r
    return by_vs, by_sym


def resolve_listing_live(
    row: Dict[str, Any],
    *,
    spot_vs: Dict[tuple, Dict[str, Any]],
    spot_sym: Dict[str, Dict[str, Any]],
    fut_vs: Dict[tuple, Dict[str, Any]],
    fut_sym: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Prefer same-venue ticker; symbol-only fallback; futures market → futures book."""
    venue = row.get("venue")
    sym = row.get("symbol")
    market = str(row.get("market") or "spot").lower()
    if market == "futures":
        live = fut_vs.get((venue, sym)) or fut_sym.get(str(sym) if sym else "")
        return live if isinstance(live, dict) else {}
    live = spot_vs.get((venue, sym)) or spot_sym.get(str(sym) if sym else "")
    if isinstance(live, dict) and live:
        return live
    # Announced as spot but only futures ticker exists (or vice versa soft).
    live = fut_vs.get((venue, sym)) or fut_sym.get(str(sym) if sym else "")
    return live if isinstance(live, dict) else {}


def score_listings(universe: Dict[str, Any], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    sec = (cfg.get("sectors") or {}).get("listings") or {}
    lookback_h = int(sec.get("lookback_hours") or 48)
    now_ms = int(time.time() * 1000)
    cutoff = now_ms - lookback_h * 3_600_000

    spot_vs, spot_sym = _index_live(
        [r for r in (universe.get("spot") or []) if isinstance(r, dict)]
    )
    fut_vs, fut_sym = _index_live(
        [r for r in (universe.get("futures") or []) if isinstance(r, dict)]
    )

    out: List[Dict[str, Any]] = []
    for row in universe.get("listings") or []:
        if not isinstance(row, dict):
            continue
        ts = row.get("listed_at_ms")
        if ts is None:
            continue
        try:
            ts_i = int(ts)
        except (TypeError, ValueError):
            continue
        if ts_i < cutoff:
            continue
        age_h = max(0, (now_ms - ts_i) // 3_600_000)
        live = resolve_listing_live(
            row, spot_vs=spot_vs, spot_sym=spot_sym, fut_vs=fut_vs, fut_sym=fut_sym
        )
        vol = live.get("quote_volume_i")
        price = live.get("price_i") or row.get("price_i")
        # Fail-closed for watch quality: no priceable live book → skip (ghost listings).
        if price is None and vol is None:
            continue
        chg_bps = live.get("change_bps")
        chg_1h = _opt_int(live.get("change_bps_1h"))
        chg_15m = _opt_int(live.get("change_bps_15m"))
        chg_4h = _opt_int(live.get("change_bps_4h"))
        risks: List[str] = []
        if vol is None:
            risks.append("no_live_volume")
        live_venue = live.get("venue")
        if live_venue and row.get("venue") and live_venue != row.get("venue"):
            risks.append("cross_venue_live")
        freshness = clamp_i(
            100 * SCORE_CENTI - mul_div(age_h * 100 * SCORE_CENTI, 1, max(lookback_h, 1))
        )
        if vol:
            vol_s = clamp_i(mul_div(vol, 40 * SCORE_CENTI, 5_000_000 * QUOTE_SCALE))
        else:
            vol_s = 0
        if chg_bps is not None:
            mom, mtf_risks = blend_mtf_momentum(int(chg_bps), chg_1h, chg_15m, chg_4h)
            risks.extend(mtf_risks)
        else:
            mom = 0
        score = clamp_i(
            mul_div(45, freshness, 100)
            + mul_div(35, vol_s, 100)
            + mul_div(20, mom, 100)
        )
        merged = {
            **row,
            "price_i": price,
            "change_bps": chg_bps,
            "quote_volume_i": vol,
            "market": row.get("market") or "spot",
        }
        metrics: Dict[str, Any] = {
            "age_hours": age_h,
            "listed_at_ms": ts_i,
            "quote_volume_i": vol,
            "change_bps": chg_bps,
            "live_venue": live_venue,
        }
        if chg_1h is not None:
            metrics["change_bps_1h"] = chg_1h
        if chg_15m is not None:
            metrics["change_bps_15m"] = chg_15m
        if chg_4h is not None:
            metrics["change_bps_4h"] = chg_4h
        out.append(
            candidate(
                sector="listings",
                row=merged,
                score_centi=score,
                setup="new-listing-watch",
                entry_hint=f"listed ~{age_h}h ago; vol_i={vol}",
                risk_flags=risks,
                metrics=metrics,
            )
        )
    return out

from __future__ import annotations

from typing import Any, Dict, List

from radar.classify.sectors import is_meme_symbol, is_stable_symbol, is_usdt_pair
from radar.money import QUOTE_SCALE, SCORE_CENTI, display_price, mul_div, price_i, quote_i
from radar.score.common import (
    blend_mtf_momentum,
    blend_volume_score_i,
    candidate,
    clamp_i,
    mtf_bias_bps,
    spread_bps_i,
)


def score_spot_alts(universe: Dict[str, Any], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    sec = (cfg.get("sectors") or {}).get("spot_alts") or {}
    min_vol_i = quote_i(sec.get("min_quote_volume_usdt") or 500_000) or (500_000 * QUOTE_SCALE)
    min_price_i = price_i(sec.get("min_price") or "0.000001") or 100
    stables = set(cfg.get("stable_bases") or [])
    memes = list(cfg.get("meme_keywords") or [])

    out: List[Dict[str, Any]] = []
    for row in universe.get("spot") or []:
        sym = row.get("symbol")
        if not is_usdt_pair(sym):
            continue
        if is_stable_symbol(sym, stables):
            continue
        if is_meme_symbol(sym, memes):
            continue
        price = row.get("price_i")
        vol = row.get("quote_volume_i")
        chg_bps = row.get("change_bps")
        if price is None or price < min_price_i or vol is None or vol < min_vol_i // 4:
            continue
        if chg_bps is None:
            continue

        chg_1h = row.get("change_bps_1h")
        chg_15m = row.get("change_bps_15m")
        chg_4h = row.get("change_bps_4h")
        if chg_1h is not None:
            chg_1h = int(chg_1h)
        if chg_15m is not None:
            chg_15m = int(chg_15m)
        if chg_4h is not None:
            chg_4h = int(chg_4h)

        momentum, risks = blend_mtf_momentum(int(chg_bps), chg_1h, chg_15m, chg_4h)
        vol_vs = row.get("vol_vs_median_bps")
        if vol_vs is not None:
            vol_vs = int(vol_vs)
            if vol_vs >= 5000:
                risks.append("vol_surge_1h")
            elif vol_vs <= -5000:
                risks.append("vol_dry_1h")
        vol_s = blend_volume_score_i(int(vol), int(min_vol_i), vol_vs)
        spr = spread_bps_i(row)
        spread_pen = 0
        if spr is not None and spr > 25:
            spread_pen = min(25 * SCORE_CENTI, (int(spr) * SCORE_CENTI) // 2)
            risks.append(f"wide_spread_{spr}bps")
        if vol < min_vol_i:
            risks.append("below_vol_floor")

        hi_i = row.get("high_i")
        lo_i = row.get("low_i")
        range_bps = None
        if (
            hi_i is not None
            and lo_i is not None
            and int(price) > 0
            and int(hi_i) >= int(lo_i)
        ):
            range_bps = mul_div(int(hi_i) - int(lo_i), 10_000, int(price))
            if range_bps > 800:
                risks.append("wide_range_24h")

        third = min(abs(int(chg_bps)), 2000) // 10
        score = clamp_i(
            mul_div(45, momentum, 100)
            + mul_div(45, vol_s, 100)
            + third
            - spread_pen
        )
        bias_bps = mtf_bias_bps(int(chg_bps), chg_1h, chg_15m)
        setup = "buy-watch" if bias_bps >= 0 else "fade-watch"
        lo = mul_div(int(price), 985, 1000)
        hi = mul_div(int(price), 1015, 1000)
        if bias_bps >= 0:
            entry = f"watch pullback near {display_price(lo)}–{display_price(int(price))}"
        else:
            entry = f"watch bounce zone {display_price(int(price))}–{display_price(hi)}"
        metrics: Dict[str, Any] = {
            "change_bps": chg_bps,
            "quote_volume_i": vol,
            "spread_bps": spr,
        }
        if chg_1h is not None:
            metrics["change_bps_1h"] = chg_1h
        if chg_15m is not None:
            metrics["change_bps_15m"] = chg_15m
        if chg_4h is not None:
            metrics["change_bps_4h"] = chg_4h
        if vol_vs is not None:
            metrics["vol_vs_median_bps"] = vol_vs
        if range_bps is not None:
            metrics["range_bps_24h"] = int(range_bps)
        out.append(
            candidate(
                sector="spot_alts",
                row=row,
                score_centi=score,
                setup=setup,
                entry_hint=entry,
                risk_flags=risks,
                metrics=metrics,
            )
        )
    return out

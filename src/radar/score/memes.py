from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from radar.money import QUOTE_SCALE, SCORE_CENTI, quote_i
from radar.score.common import (
    blend_dex_momentum,
    candidate,
    clamp_i,
    dex_bias_bps,
    log_volume_score_i,
)


def score_memes(universe: Dict[str, Any], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    sec = (cfg.get("sectors") or {}).get("memes") or {}
    min_liq_i = quote_i(sec.get("min_liquidity_usd") or 50_000) or (50_000 * QUOTE_SCALE)
    max_age_h = int(sec.get("max_age_hours") or 168)
    now_ms = int(time.time() * 1000)

    out: List[Dict[str, Any]] = []
    for row in universe.get("memes") or []:
        liq = row.get("liquidity_i")
        vol = row.get("quote_volume_i")
        chg_24 = row.get("change_bps")
        chg_1h = _opt_int(row.get("change_bps_1h"))
        chg_5m = _opt_int(row.get("change_bps_5m"))
        # Prefer 24h as base; fall back to 1h if Dex only sent h1
        if chg_24 is None and chg_1h is not None:
            chg_24 = chg_1h
        if chg_24 is None:
            continue
        chg_24 = int(chg_24)

        created = row.get("pair_created_at_ms")
        risks: List[str] = []
        if liq is None or liq < min_liq_i // 2:
            continue
        if liq < min_liq_i:
            risks.append("thin_liquidity")

        age_h = None
        if created:
            age_h = max(0, (now_ms - int(created)) // 3_600_000)
            if age_h > max_age_h:
                risks.append("aged_pair")

        liq_s = log_volume_score_i(int(liq), int(min_liq_i))
        vol_s = log_volume_score_i(vol, int(min_liq_i))
        mom, mtf_risks = blend_dex_momentum(chg_24, chg_1h, chg_5m)
        risks.extend(mtf_risks)
        if age_h is not None and age_h < 24:
            age_bonus = 25 * SCORE_CENTI
        elif age_h is not None and age_h < 72:
            age_bonus = 18 * SCORE_CENTI
        else:
            age_bonus = 10 * SCORE_CENTI

        score = clamp_i(
            mul_div_safe(30, liq_s)
            + mul_div_safe(30, vol_s)
            + mul_div_safe(25, mom)
            + mul_div_safe(15, age_bonus)
        )
        bias = dex_bias_bps(chg_24, chg_1h, chg_5m)
        setup = "buy-watch" if bias >= 0 else "fade-watch"
        entry = (
            f"DEX watch; liq_i={liq}; age_h={age_h}"
            if age_h is not None
            else f"DEX watch; liq_i={liq}"
        )
        metrics: Dict[str, Any] = {
            "liquidity_i": liq,
            "quote_volume_i": vol,
            "change_bps": chg_24,
            "age_hours": age_h,
            "chain": row.get("chain"),
        }
        if chg_1h is not None:
            metrics["change_bps_1h"] = chg_1h
        if chg_5m is not None:
            metrics["change_bps_5m"] = chg_5m
        out.append(
            candidate(
                sector="memes",
                row=row,
                score_centi=score,
                setup=setup,
                entry_hint=entry,
                risk_flags=risks,
                metrics=metrics,
            )
        )
    return out


def mul_div_safe(pct: int, v: int) -> int:
    return (pct * int(v)) // 100


def _opt_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    return int(v)

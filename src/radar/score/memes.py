from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Set

from radar.classify.sectors import is_meme_symbol, is_stable_symbol, is_usdt_pair
from radar.money import QUOTE_SCALE, SCORE_CENTI, display_price, mul_div, quote_i
from radar.score.common import (
    blend_dex_momentum,
    blend_mtf_momentum,
    blend_volume_score_i,
    candidate,
    clamp_i,
    dex_bias_bps,
    log_volume_score_i,
    mtf_bias_bps,
)


_CEX_VENUES = frozenset({"kucoin", "binance", "bybit", "mexc"})


def score_memes(universe: Dict[str, Any], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    sec = (cfg.get("sectors") or {}).get("memes") or {}
    min_liq_i = quote_i(sec.get("min_liquidity_usd") or 50_000) or (50_000 * QUOTE_SCALE)
    max_age_h = int(sec.get("max_age_hours") or 168)
    now_ms = int(time.time() * 1000)
    include_cex = bool(sec.get("include_cex_spot", True))
    min_cex_vol = quote_i(sec.get("min_cex_quote_volume_usdt") or 200_000) or (
        200_000 * QUOTE_SCALE
    )
    keywords = list(cfg.get("meme_keywords") or [])
    stables = set(cfg.get("stable_bases") or [])

    out: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    for row in universe.get("memes") or []:
        liq = row.get("liquidity_i")
        vol = row.get("quote_volume_i")
        chg_24 = row.get("change_bps")
        chg_1h = _opt_int(row.get("change_bps_1h"))
        chg_5m = _opt_int(row.get("change_bps_5m"))
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
            "source": "dex",
        }
        if chg_1h is not None:
            metrics["change_bps_1h"] = chg_1h
        if chg_5m is not None:
            metrics["change_bps_5m"] = chg_5m
        sym = str(row.get("symbol") or "")
        if sym:
            seen.add(sym.upper())
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

    if include_cex and keywords:
        # Prefer KuCoin when the same meme base appears on multiple CEX books.
        venue_rank = {"kucoin": 0, "binance": 1, "bybit": 2, "mexc": 3}
        cex_best: Dict[str, Dict[str, Any]] = {}
        for row in universe.get("spot") or []:
            if not isinstance(row, dict):
                continue
            venue = str(row.get("venue") or "").lower()
            if venue not in _CEX_VENUES:
                continue
            sym = row.get("symbol")
            if not is_usdt_pair(sym):
                continue
            if is_stable_symbol(sym, stables):
                continue
            if not is_meme_symbol(sym, keywords):
                continue
            sym_u = str(sym).upper()
            if sym_u in seen:
                continue
            price = row.get("price_i")
            vol = row.get("quote_volume_i")
            chg_bps = row.get("change_bps")
            if price is None or vol is None or chg_bps is None:
                continue
            if int(vol) < int(min_cex_vol) // 4:
                continue
            prev = cex_best.get(sym_u)
            if prev is None or venue_rank.get(venue, 9) < venue_rank.get(
                str(prev.get("venue") or "").lower(), 9
            ):
                cex_best[sym_u] = row

        for sym_u, row in cex_best.items():
            vol = int(row.get("quote_volume_i") or 0)
            chg_bps = int(row.get("change_bps") or 0)
            chg_1h = _opt_int(row.get("change_bps_1h"))
            chg_15m = _opt_int(row.get("change_bps_15m"))
            chg_4h = _opt_int(row.get("change_bps_4h"))
            risks: List[str] = []
            mom, mtf_risks = blend_mtf_momentum(chg_bps, chg_1h, chg_15m, chg_4h)
            risks.extend(mtf_risks)
            vol_vs = row.get("vol_vs_median_bps")
            if vol_vs is not None:
                vol_vs = int(vol_vs)
            vol_s = blend_volume_score_i(vol, int(min_cex_vol), vol_vs)
            if vol < int(min_cex_vol):
                risks.append("below_vol_floor")
            score = clamp_i(mul_div(50, mom, 100) + mul_div(50, vol_s, 100))
            bias = mtf_bias_bps(chg_bps, chg_1h, chg_15m)
            setup = "buy-watch" if bias >= 0 else "fade-watch"
            price = int(row.get("price_i") or 0)
            lo = mul_div(price, 985, 1000) if price else 0
            entry = (
                f"CEX meme ({row.get('venue')}); pullback near "
                f"{display_price(lo)}–{display_price(price)}"
                if bias >= 0
                else f"CEX meme ({row.get('venue')}); fade watch @ {display_price(price)}"
            )
            metrics = {
                "change_bps": chg_bps,
                "quote_volume_i": vol,
                "source": "cex",
                "venue": row.get("venue"),
            }
            if chg_1h is not None:
                metrics["change_bps_1h"] = chg_1h
            if chg_15m is not None:
                metrics["change_bps_15m"] = chg_15m
            if chg_4h is not None:
                metrics["change_bps_4h"] = chg_4h
            if vol_vs is not None:
                metrics["vol_vs_median_bps"] = vol_vs
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
            seen.add(sym_u)

    return out


def mul_div_safe(pct: int, v: int) -> int:
    return (pct * int(v)) // 100


def _opt_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    return int(v)

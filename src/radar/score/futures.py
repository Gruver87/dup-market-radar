from __future__ import annotations

from typing import Any, Dict, List, Optional

from radar.classify.sectors import is_non_crypto_futures_symbol, is_usdt_pair
from radar.money import QUOTE_SCALE, SCORE_CENTI, display_price, mul_div, price_i, quote_i
from radar.score.common import (
    blend_mtf_momentum,
    candidate,
    clamp_i,
    log_volume_score_i,
    mtf_bias_bps,
)


def score_futures(universe: Dict[str, Any], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    sec = (cfg.get("sectors") or {}).get("futures") or {}
    min_oi_i = quote_i(sec.get("min_open_interest_usdt") or 1_000_000) or (1_000_000 * QUOTE_SCALE)
    # extreme funding 0.0005 → * 1e8 = 50_000
    extreme_fr_i = int(
        sec.get("extreme_funding_rate_i")
        or price_i(sec.get("extreme_funding_abs") or "0.0005")
        or 50_000
    )

    out: List[Dict[str, Any]] = []
    for row in universe.get("futures") or []:
        sym = row.get("symbol")
        if not is_usdt_pair(sym):
            continue
        extra = sec.get("exclude_bases") or []
        if is_non_crypto_futures_symbol(sym, extra):
            continue
        price = row.get("price_i")
        chg_bps = row.get("change_bps")
        vol = row.get("quote_volume_i")
        fr_i = row.get("funding_rate_i")
        oi = row.get("open_interest_i")
        if price is None or chg_bps is None:
            continue

        chg_1h = _opt_int(row.get("change_bps_1h"))
        chg_15m = _opt_int(row.get("change_bps_15m"))
        chg_4h = _opt_int(row.get("change_bps_4h"))

        mom, risks = blend_mtf_momentum(int(chg_bps), chg_1h, chg_15m, chg_4h)
        if oi is not None and oi > 0:
            oi_s = log_volume_score_i(int(oi), int(min_oi_i))
            if oi < min_oi_i:
                risks.append("low_oi")
        else:
            oi_s = 0
            risks.append("oi_missing")

        vol_s = log_volume_score_i(vol, 1_000_000 * QUOTE_SCALE)

        bias = mtf_bias_bps(int(chg_bps), chg_1h, chg_15m)
        side = "long" if bias >= 0 else "short"
        if fr_i is not None:
            if fr_i >= extreme_fr_i:
                risks.append("extreme_positive_funding")
                if bias > 0:
                    side = "short"
            elif fr_i <= -extreme_fr_i:
                risks.append("extreme_negative_funding")
                if bias < 0:
                    side = "long"

        confluence = 15 * SCORE_CENTI
        if oi is not None and oi >= min_oi_i:
            confluence += 20 * SCORE_CENTI

        score = clamp_i(
            mul_div(35, mom, 100)
            + mul_div(30, oi_s, 100)
            + mul_div(25, vol_s, 100)
            + mul_div(10, confluence, 100)
        )
        entry = (
            f"{side} watch; mark≈{display_price(int(price))}; funding_i={fr_i}"
            if fr_i is not None
            else f"{side} watch; last≈{display_price(int(price))}"
        )
        metrics: Dict[str, Any] = {
            "change_bps": chg_bps,
            "funding_rate_i": fr_i,
            "open_interest_i": oi,
            "quote_volume_i": vol,
        }
        if chg_1h is not None:
            metrics["change_bps_1h"] = chg_1h
        if chg_15m is not None:
            metrics["change_bps_15m"] = chg_15m
        if chg_4h is not None:
            metrics["change_bps_4h"] = chg_4h
        out.append(
            candidate(
                sector="futures",
                row=row,
                score_centi=score,
                setup=side,
                entry_hint=entry,
                risk_flags=risks,
                metrics=metrics,
            )
        )
    return out


def _opt_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    return int(v)

from __future__ import annotations

from typing import Any, Dict, List

from radar.money import QUOTE_SCALE, SCORE_CENTI, display_price, mul_div, pct_to_bps
from radar.score.common import candidate, clamp_i


def score_stables(universe: Dict[str, Any], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    sec = (cfg.get("sectors") or {}).get("stables") or {}
    warn_bps = int(sec.get("depeg_warn_bps") or 0) or (
        pct_to_bps(sec.get("depeg_warn_pct") or 0.3) or 30
    )

    out: List[Dict[str, Any]] = []
    for row in universe.get("stables") or []:
        price = row.get("price_i")
        peg_bps = row.get("peg_deviation_bps")
        if price is None or peg_bps is None:
            continue
        risks: List[str] = []
        if peg_bps >= warn_bps:
            risks.append("depeg_warn")
        vol = int(row.get("quote_volume_i") or 0)
        # peg%*40 → (peg_bps/100)*40*SCORE_CENTI = peg_bps * SCORE_CENTI * 40 / 100
        peg_term = mul_div(int(peg_bps) * 40, SCORE_CENTI, 100)
        # min(vol_usdt/1e9, 20) → vol_i/(1e9*QUOTE_SCALE)
        vol_usdt_units = vol // max(QUOTE_SCALE, 1)
        vol_term = min(vol_usdt_units // 1_000_000_000, 20) * SCORE_CENTI
        score = clamp_i(peg_term + vol_term)
        entry = f"peg {display_price(int(price))} (|dev|={peg_bps}bps) — watch only"
        out.append(
            candidate(
                sector="stables",
                row=row,
                score_centi=score,
                setup="peg-monitor",
                entry_hint=entry,
                risk_flags=risks,
                metrics={
                    "price_i": price,
                    "peg_deviation_bps": peg_bps,
                    "quote_volume_i": vol,
                },
            )
        )
    return out

from __future__ import annotations

from typing import Any, Dict, List, Optional

from radar.money import QUOTE_SCALE, SCORE_CENTI, quote_i
from radar.score.common import candidate, clamp_i, mtf_bias_bps


def score_whales(universe: Dict[str, Any], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Size-proxy whale sector — aggregate prints then apply min notional.

    Per-print ≥ min_notional (legacy) painted empty under KuCoin histories
    (recent tape rarely has a single ≥100k USDT print). Correct semantics:
    sum by (venue, symbol, side), drop dust prints, then gate the bucket.
    """
    sec = (cfg.get("sectors") or {}).get("whales") or {}
    min_n = quote_i(sec.get("min_notional_usdt") or 10_000) or (10_000 * QUOTE_SCALE)
    # Dust floor: skip sub-print noise; never above min_n / 50.
    dust_cfg = sec.get("min_print_usdt")
    if dust_cfg is not None:
        dust = quote_i(dust_cfg) or 0
    else:
        dust = max(100 * QUOTE_SCALE, min_n // 50)

    # Spot short-window join (BTC/ETH/SOL usually in sampled top-N) — no extra HTTP.
    spot_by_sym: Dict[str, Dict[str, Any]] = {}
    for r in universe.get("spot") or []:
        sym = r.get("symbol")
        if isinstance(sym, str) and sym:
            spot_by_sym[sym] = r

    buckets: Dict[tuple, Dict[str, Any]] = {}
    for row in universe.get("whales") or []:
        notional = int(row.get("notional_i") or 0)
        if notional < dust:
            continue
        key = (row.get("venue"), row.get("symbol"), row.get("side") or "unknown")
        b = buckets.get(key)
        if not b:
            b = {
                "venue": row.get("venue"),
                "symbol": row.get("symbol"),
                "side": row.get("side"),
                "notional_i": 0,
                "prints": 0,
                "price_i": row.get("price_i"),
                "market": "spot",
            }
            buckets[key] = b
        b["notional_i"] += notional
        b["prints"] += 1
        b["price_i"] = row.get("price_i") or b["price_i"]

    out: List[Dict[str, Any]] = []
    for b in buckets.values():
        if int(b["notional_i"]) < min_n:
            continue
        # min(notional/min_n, 20) * 5 score → centi
        ratio = min(b["notional_i"] // max(min_n, 1), 20)
        score = clamp_i(ratio * 5 * SCORE_CENTI)
        side = (b.get("side") or "unknown").lower()
        setup = f"whale-{side}"
        risks: List[str] = ["size_proxy_only"]
        metrics: Dict[str, Any] = {
            "notional_i": b["notional_i"],
            "prints": b["prints"],
            "side": side,
        }
        spot = spot_by_sym.get(str(b.get("symbol") or ""))
        if spot is not None:
            chg = _opt_int(spot.get("change_bps"))
            chg_1h = _opt_int(spot.get("change_bps_1h"))
            chg_15m = _opt_int(spot.get("change_bps_15m"))
            if chg is not None:
                bias = mtf_bias_bps(chg, chg_1h, chg_15m)
                metrics["spot_bias_bps"] = bias
                if chg_15m is not None:
                    metrics["change_bps_15m"] = chg_15m
                if chg_1h is not None:
                    metrics["change_bps_1h"] = chg_1h
                whale_buy = side == "buy"
                whale_sell = side == "sell"
                if whale_buy and bias > 0:
                    score = clamp_i(score + 8 * SCORE_CENTI)
                    risks.append("spot_mtf_align")
                elif whale_sell and bias < 0:
                    score = clamp_i(score + 8 * SCORE_CENTI)
                    risks.append("spot_mtf_align")
                elif (whale_buy and bias < 0) or (whale_sell and bias > 0):
                    risks.append("spot_mtf_diverge")
        out.append(
            candidate(
                sector="whales",
                row=b,
                score_centi=score,
                setup=setup,
                entry_hint=(
                    f"size-based print sum micros={b['notional_i']} "
                    f"({b['prints']} trades) — not labeled smart money"
                ),
                risk_flags=risks,
                metrics=metrics,
            )
        )
    return out


def _opt_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    return int(v)

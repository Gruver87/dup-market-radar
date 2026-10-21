from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from radar.money import SCORE_CENTI, mul_div


def clamp_i(x: int, lo: int = 0, hi: int = 100 * SCORE_CENTI) -> int:
    return max(lo, min(hi, int(x)))


def blend_mtf_momentum(
    chg_bps: int,
    chg_1h: Optional[int],
    chg_15m: Optional[int],
    chg_4h: Optional[int] = None,
) -> Tuple[int, List[str]]:
    """Weighted |momentum| in score-centi; diverge risk flags (spot + futures)."""
    risks: List[str] = []
    mom_24 = (abs(int(chg_bps)) * 5) // 2
    mom_1h = (abs(int(chg_1h)) * 5) // 2 if chg_1h is not None else None
    mom_15 = (abs(int(chg_15m)) * 5) // 2 if chg_15m is not None else None
    mom_4h = (abs(int(chg_4h)) * 5) // 2 if chg_4h is not None else None
    if chg_1h is not None and (int(chg_bps) >= 0) != (int(chg_1h) >= 0):
        risks.append("mtf_diverge_1h")
    if chg_15m is not None and (int(chg_bps) >= 0) != (int(chg_15m) >= 0):
        risks.append("mtf_diverge_15m")
    if chg_4h is not None and (int(chg_bps) >= 0) != (int(chg_4h) >= 0):
        risks.append("mtf_diverge_4h")

    if mom_1h is not None and mom_15 is not None and mom_4h is not None:
        momentum = (mom_24 * 30 + mom_4h * 20 + mom_1h * 30 + mom_15 * 20) // 100
    elif mom_1h is not None and mom_15 is not None:
        momentum = (mom_24 * 40 + mom_1h * 35 + mom_15 * 25) // 100
    elif mom_1h is not None and mom_4h is not None:
        momentum = (mom_24 * 45 + mom_4h * 20 + mom_1h * 35) // 100
    elif mom_15 is not None and mom_4h is not None:
        momentum = (mom_24 * 45 + mom_4h * 20 + mom_15 * 35) // 100
    elif mom_1h is not None:
        momentum = (mom_24 * 55 + mom_1h * 45) // 100
    elif mom_15 is not None:
        momentum = (mom_24 * 60 + mom_15 * 40) // 100
    elif mom_4h is not None:
        momentum = (mom_24 * 70 + mom_4h * 30) // 100
    else:
        momentum = mom_24
    return clamp_i(momentum), risks


def mtf_bias_bps(
    chg_bps: int,
    chg_1h: Optional[int],
    chg_15m: Optional[int],
) -> int:
    """Prefer shorter window when move is material (4h never drives setup/side)."""
    if chg_15m is not None and abs(int(chg_15m)) >= 30:
        return int(chg_15m)
    if chg_1h is not None and abs(int(chg_1h)) >= 50:
        return int(chg_1h)
    return int(chg_bps)


def blend_dex_momentum(
    chg_bps: int,
    chg_1h: Optional[int],
    chg_5m: Optional[int],
) -> Tuple[int, List[str]]:
    """DexScreener h24 / h1 / m5 blend (no candle GET). Flags mtf_diverge_{1h,5m}."""
    risks: List[str] = []
    mom_24 = (abs(int(chg_bps)) * 5) // 2
    mom_1h = (abs(int(chg_1h)) * 5) // 2 if chg_1h is not None else None
    mom_5m = (abs(int(chg_5m)) * 5) // 2 if chg_5m is not None else None
    if chg_1h is not None and (int(chg_bps) >= 0) != (int(chg_1h) >= 0):
        risks.append("mtf_diverge_1h")
    if chg_5m is not None and (int(chg_bps) >= 0) != (int(chg_5m) >= 0):
        risks.append("mtf_diverge_5m")
    if mom_1h is not None and mom_5m is not None:
        momentum = (mom_24 * 40 + mom_1h * 35 + mom_5m * 25) // 100
    elif mom_1h is not None:
        momentum = (mom_24 * 55 + mom_1h * 45) // 100
    elif mom_5m is not None:
        momentum = (mom_24 * 60 + mom_5m * 40) // 100
    else:
        momentum = mom_24
    return clamp_i(momentum), risks


def dex_bias_bps(
    chg_bps: int,
    chg_1h: Optional[int],
    chg_5m: Optional[int],
) -> int:
    """Prefer 5m / 1h when material for DEX setups."""
    if chg_5m is not None and abs(int(chg_5m)) >= 25:
        return int(chg_5m)
    if chg_1h is not None and abs(int(chg_1h)) >= 50:
        return int(chg_1h)
    return int(chg_bps)


def spread_bps_i(row: Dict[str, Any]) -> Optional[int]:
    bid = row.get("bid_i")
    ask = row.get("ask_i")
    price = row.get("price_i")
    if bid and ask and price and price > 0 and ask >= bid:
        return mul_div(ask - bid, 10_000, price)
    return None


def log_volume_score_i(vol_i: Optional[int], floor_i: int) -> int:
    """Return score in centi-points (0..10000)."""
    if vol_i is None or vol_i <= 0 or floor_i <= 0:
        return 0
    if vol_i < floor_i:
        return clamp_i(mul_div(20 * SCORE_CENTI, vol_i, floor_i))
    # 40 + 15 * log10(vol/floor)  → approximate with integer log10
    ratio = mul_div(vol_i, 1_000_000, floor_i)  # scaled 1e6
    # log10(ratio/1e6) ≈ digits
    log10_centi = _log10_centi(ratio) - 600  # log10(1e6)=6 → 600 centi
    return clamp_i(40 * SCORE_CENTI + mul_div(15 * SCORE_CENTI, log10_centi, 100))


def vol_relative_score_i(vs_median_bps: int) -> int:
    """Own-history surge score: 0 bps (at median) → 5000; ±10000 → ±2500 around mid."""
    return clamp_i(50 * SCORE_CENTI + int(vs_median_bps) // 4)


def blend_volume_score_i(
    vol_i: Optional[int], floor_i: int, vs_median_bps: Optional[int]
) -> int:
    """Absolute log(vol/floor) blended with own-history ratio when present."""
    abs_s = log_volume_score_i(vol_i, floor_i)
    if vs_median_bps is None:
        return abs_s
    rel_s = vol_relative_score_i(int(vs_median_bps))
    return clamp_i((abs_s * 55 + rel_s * 45) // 100)


def _log10_centi(x: int) -> int:
    """log10(x) * 100 as int, x > 0."""
    if x <= 0:
        return 0
    # count digits
    n = 0
    v = x
    while v >= 10:
        v //= 10
        n += 1
    # rough: n + frac from leading digits
    # leading 2 digits
    lead = x
    while lead >= 100:
        lead //= 10
    # log10(lead/10) table-ish: lead 10..99 → 0..100 centi approx
    frac = mul_div(lead - 10, 100, 90)  # 0..100
    return n * 100 + frac


def _cap_flags(flags: List[str], *, max_n: int = 8, max_len: int = 64) -> List[str]:
    out: List[str] = []
    for f in list(flags or [])[:max_n]:
        s = str(f).strip()[:max_len]
        if s:
            out.append(s)
    return out


def _cap_metrics(metrics: Dict[str, Any], *, max_keys: int = 32) -> Dict[str, Any]:
    if not isinstance(metrics, dict):
        return {}
    out: Dict[str, Any] = {}
    for i, (k, v) in enumerate(metrics.items()):
        if i >= max_keys:
            break
        key = str(k)[:64]
        if isinstance(v, str):
            out[key] = v[:256]
        elif isinstance(v, (int, bool)) or v is None:
            out[key] = v
        elif isinstance(v, float):
            # Scores already integer; refuse float money leakage into metrics
            continue
        else:
            out[key] = str(v)[:128]
    return out


def candidate(
    *,
    sector: str,
    row: Dict[str, Any],
    score_centi: int,
    setup: str,
    entry_hint: str,
    risk_flags: List[str],
    metrics: Dict[str, Any],
) -> Dict[str, Any]:
    url = row.get("url")
    if isinstance(url, str):
        url = url.strip()[:256]
        if not url.startswith("https://"):
            url = None
    chain = row.get("chain")
    if isinstance(chain, str):
        chain = chain.strip()[:32]
    return {
        "sector": str(sector)[:32],
        "symbol": row.get("symbol"),
        "venue": row.get("venue"),
        "price_i": row.get("price_i"),
        "change_bps": row.get("change_bps"),
        "quote_volume_i": row.get("quote_volume_i") or row.get("liquidity_i"),
        "score_centi": int(score_centi),
        "setup": str(setup or "")[:64],
        "entry_hint": str(entry_hint or "")[:256],
        "risk_flags": _cap_flags(risk_flags),
        "metrics": _cap_metrics(metrics),
        "market": row.get("market"),
        "chain": chain,
        "url": url,
        # money_unit honesty for UI
        "scales": {
            "price": 100_000_000,
            "quote": 1_000_000,
            "score_centi": 100,
            "change_bps": 100,
        },
    }

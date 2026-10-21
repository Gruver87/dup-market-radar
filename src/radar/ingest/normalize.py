"""Attach integer fixed-point fields; drop float money after parse."""

from __future__ import annotations

from typing import Any, Dict, Optional

from radar.money import (
    PRICE_SCALE,
    base_i,
    fraction_to_bps,
    notional_i_from_price_qty,
    pct_to_bps,
    peg_deviation_bps_from_price,
    price_i,
    quote_i,
)


_MAX_SYM_CHARS = 32
_MAX_VENUE_CHARS = 32
_MAX_SETUP_CHARS = 64


def enrich_market_row(row: Dict[str, Any], *, change_is_fraction: bool = False) -> Dict[str, Any]:
    """Convert API floats/strings → *_i / *_bps integers. Removes float money keys."""
    out = dict(row)
    # Bound identity strings early — venue dumps must not grow unbounded
    if isinstance(out.get("symbol"), str):
        out["symbol"] = out["symbol"].strip().upper()[:_MAX_SYM_CHARS]
    elif out.get("symbol") is not None:
        out["symbol"] = str(out["symbol"])[:_MAX_SYM_CHARS]
    if isinstance(out.get("symbol_raw"), str):
        out["symbol_raw"] = out["symbol_raw"][:_MAX_SYM_CHARS]
    if isinstance(out.get("venue"), str):
        out["venue"] = out["venue"].strip().lower()[:_MAX_VENUE_CHARS]
    if isinstance(out.get("setup"), str):
        out["setup"] = out["setup"].strip()[:_MAX_SETUP_CHARS]
    out["price_i"] = price_i(out.get("price"))
    out["bid_i"] = price_i(out.get("bid"))
    out["ask_i"] = price_i(out.get("ask"))
    out["high_i"] = price_i(out.get("high"))
    out["low_i"] = price_i(out.get("low"))
    out["mark_price_i"] = price_i(out.get("mark_price"))
    out["index_price_i"] = price_i(out.get("index_price"))
    out["quote_volume_i"] = quote_i(out.get("quote_volume"))
    out["liquidity_i"] = quote_i(out.get("liquidity_usd"))
    out["open_interest_i"] = quote_i(out.get("open_interest_usdt") or out.get("open_interest"))
    # funding: fraction → bps (0.0005 → 5 bps of rate*10000? funding 0.0001 = 1 bps of notional)
    # store funding as micros of rate: rate * 1e8
    out["funding_rate_i"] = price_i(out.get("funding_rate"))  # 1e8 scale of fraction
    chg = out.get("change_pct")
    if change_is_fraction:
        out["change_bps"] = fraction_to_bps(chg)
    else:
        out["change_bps"] = pct_to_bps(chg)
    if out.get("change_pct_1h") is not None:
        out["change_bps_1h"] = pct_to_bps(out.get("change_pct_1h"))
    if out.get("change_pct_15m") is not None:
        out["change_bps_15m"] = pct_to_bps(out.get("change_pct_15m"))
    if out.get("change_pct_4h") is not None:
        out["change_bps_4h"] = pct_to_bps(out.get("change_pct_4h"))
    if out.get("change_pct_5m") is not None:
        out["change_bps_5m"] = pct_to_bps(out.get("change_pct_5m"))
    if out.get("peg_deviation_pct") is not None:
        out["peg_deviation_bps"] = pct_to_bps(out.get("peg_deviation_pct"))
    elif out.get("price_i") is not None and out.get("market") == "stable":
        out["peg_deviation_bps"] = peg_deviation_bps_from_price(int(out["price_i"]), PRICE_SCALE)
    if out.get("qty") is not None:
        out["qty_i"] = base_i(out.get("qty"))
    if out.get("notional_usdt") is not None:
        out["notional_i"] = quote_i(out.get("notional_usdt"))
    elif out.get("price_i") is not None and out.get("qty_i") is not None:
        out["notional_i"] = notional_i_from_price_qty(int(out["price_i"]), int(out["qty_i"]))

    for k in (
        "price",
        "bid",
        "ask",
        "high",
        "low",
        "mark_price",
        "index_price",
        "quote_volume",
        "liquidity_usd",
        "open_interest",
        "open_interest_usdt",
        "funding_rate",
        "change_pct",
        "change_pct_1h",
        "change_pct_15m",
        "change_pct_4h",
        "change_pct_5m",
        "peg_deviation_pct",
        "notional_usdt",
        "qty",
    ):
        out.pop(k, None)
    return out


# Soft cap venue dumps before scoring (spot allTickers is large but << this).
_MAX_ENRICH_ROWS = 50_000


def enrich_list(rows: Optional[list], **kwargs: Any) -> list:
    if not rows:
        return []
    capped = rows[:_MAX_ENRICH_ROWS]
    return [
        enrich_market_row(r, **kwargs) if isinstance(r, dict) else r for r in capped
    ]

"""Nearest analysis entry/exit — heuristic zones from price_i (not order-book fills)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from radar.money import display_price, drop_bps, mul_div


# Pullback / bounce bands match dashboard (1.5%)
PULLBACK_BPS = 150  # 1.5% below mark for buy/long entry zone
BOUNCE_BPS = 150  # 1.5% above mark for sell/exit / short entry zone


def _zone_lo(price_i: int, bps: int = PULLBACK_BPS) -> int:
    return mul_div(int(price_i), 10_000 - int(bps), 10_000)


def _zone_hi(price_i: int, bps: int = BOUNCE_BPS) -> int:
    return mul_div(int(price_i), 10_000 + int(bps), 10_000)


def nearest_for_action(
    *,
    action: str,
    price_i: int,
    setup: Optional[str] = None,
    entry_price_i: Optional[int] = None,
) -> Dict[str, Any]:
    """Compute nearest analysis point for buy/sell/long/short/close.

    Honesty: this is Radar zone math from last mark — not KuCoin book top.
    """
    action = (action or "").lower().strip()[:32]
    mark = int(price_i)
    if mark <= 0:
        return {"ok": False, "error": "price_i missing"}

    lo = _zone_lo(mark)
    hi = _zone_hi(mark)
    setup = (setup or "").lower()[:64]

    if action in {"buy", "long", "open_long"}:
        # Prefer pullback toward lo; if already below mid of zone, nearest = mark
        mid = (lo + mark) // 2
        nearest = mark if mark <= mid else lo
        kind = "entry_long"
        hint = f"зона входа лонг {display_price(lo)}–{display_price(mark)}"
    elif action in {"sell", "exit_long", "close_long", "take_profit"}:
        mid = (mark + hi) // 2
        nearest = mark if mark >= mid else hi
        kind = "exit_long"
        hint = f"зона выхода/слива {display_price(mark)}–{display_price(hi)}"
    elif action in {"short", "open_short"}:
        mid = (mark + hi) // 2
        nearest = mark if mark >= mid else hi
        kind = "entry_short"
        hint = f"зона входа шорт {display_price(mark)}–{display_price(hi)}"
    elif action in {"cover", "exit_short", "close_short"}:
        mid = (lo + mark) // 2
        nearest = mark if mark <= mid else lo
        kind = "exit_short"
        hint = f"зона закрытия шорта {display_price(lo)}–{display_price(mark)}"
    else:
        return {"ok": False, "error": f"unknown action: {action}"}

    # Setup-aware nudge
    if setup == "fade-watch" and kind.startswith("entry_long"):
        nearest = lo
        hint += " · сейчас fade — лучше ждать откат к низу зоны"
    if setup in {"buy-watch", "long", "new-listing-watch"} and kind == "exit_long":
        nearest = hi
        hint += " · тренд ещё up-watch — выход ближе к верху зоны"
    if setup == "short" and kind == "entry_short":
        nearest = hi
    if setup == "long" and kind == "entry_long":
        nearest = lo

    pnl_bps = None
    if entry_price_i and int(entry_price_i) > 0:
        if kind in {"exit_long", "entry_short"}:
            # long PnL: mark vs entry (negative drop_bps = profit conceptually inverted)
            pnl_bps = -drop_bps(int(entry_price_i), mark)
        elif kind in {"exit_short", "entry_long"}:
            pnl_bps = drop_bps(int(entry_price_i), mark)

    return {
        "ok": True,
        "basis": "analysis_zone",  # NOT exchange fill / book
        "action": action,
        "kind": kind,
        "mark_price_i": mark,
        "zone_lo_i": lo,
        "zone_hi_i": hi,
        "nearest_price_i": int(nearest),
        "nearest_display": display_price(int(nearest)),
        "mark_display": display_price(mark),
        "zone_lo_display": display_price(lo),
        "zone_hi_display": display_price(hi),
        "setup": setup or None,
        "hint": hint,
        "pnl_bps_vs_entry": pnl_bps,
        "disclaimer": "analysis nearest — не гарантия исполнения на бирже",
    }

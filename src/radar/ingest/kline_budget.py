"""Per-scan kline GET budget — hard ceiling on ensure waterfall."""

from __future__ import annotations

from typing import Any, Optional


def init_kline_budget(http: Any, max_gets: Optional[int]) -> None:
    """Attach budget counters onto the shared AllowlistHttp for one scan."""
    http.kline_gets = 0
    if max_gets is None:
        http.kline_budget_left = None
    else:
        http.kline_budget_left = max(0, int(max_gets))


def budget_consume(http: Any) -> bool:
    """Return False when budget exhausted (caller must skip GET)."""
    left = getattr(http, "kline_budget_left", None)
    gets = int(getattr(http, "kline_gets", 0) or 0) + 1
    if left is not None:
        if int(left) <= 0:
            return False
        http.kline_budget_left = int(left) - 1
    http.kline_gets = gets
    return True


def budget_status(http: Any) -> dict:
    return {
        "kline_gets": int(getattr(http, "kline_gets", 0) or 0),
        "kline_budget_left": getattr(http, "kline_budget_left", None),
    }

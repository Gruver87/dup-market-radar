"""Exchange-string parse helpers — no float money math.

Keep raw API strings for enrich → *_i. Decimal only when a venue
sends a fraction that must become a percent string for pct_to_bps.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Optional


# Soft refuse absurd exchange number strings (memory / Decimal DoS).
_MAX_NUM_CHARS = 64


def keep_num(v: Any) -> Optional[str]:
    """Pass through exchange number as string; None if missing/empty."""
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, str):
        s = v.strip()
        if not s or len(s) > _MAX_NUM_CHARS:
            return None
        return s
    # int/Decimal from JSON — stringify without float
    if isinstance(v, int):
        s = str(v)
        return s if len(s) <= _MAX_NUM_CHARS else None
    if isinstance(v, Decimal):
        s = format(v, "f")
        return s if len(s) <= _MAX_NUM_CHARS else None
    # JSON floats sometimes slip through — stringify via Decimal(str)
    try:
        raw = str(v).strip()
        if len(raw) > _MAX_NUM_CHARS:
            return None
        return format(Decimal(raw), "f")
    except (InvalidOperation, ValueError, TypeError):
        return None


def fraction_to_pct_str(v: Any, *, already_pct_if_abs_ge: int = 2) -> Optional[str]:
    """Venue fraction (0.05 = 5%) → percent string. Leave alone if already %-like."""
    s = keep_num(v)
    if s is None:
        return None
    try:
        d = Decimal(s)
    except (InvalidOperation, ValueError):
        return None
    if abs(d) >= already_pct_if_abs_ge:
        return format(d, "f")
    return format(d * Decimal(100), "f")


def quote_volume_sort_key(row: dict) -> int:
    """Integer micros for sorting (0 if unparsable) — no float sort keys."""
    from radar.money import quote_i

    return int(quote_i(row.get("quote_volume")) or 0)


def abs_change_bps_sort_key(row: dict) -> int:
    """|change_bps| for short-window sample ranking — integer only."""
    from radar.money import pct_to_bps

    raw = row.get("change_bps")
    if raw is not None:
        try:
            return abs(int(raw))
        except (TypeError, ValueError):
            pass
    bps = pct_to_bps(row.get("change_pct"))
    return abs(int(bps)) if bps is not None else 0

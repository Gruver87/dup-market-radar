"""Cross-venue row dedupe — keep highest-priority venue per symbol."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple


def dedupe_by_symbol(
    rows: List[Dict[str, Any]],
    venue_priority: Dict[str, int],
    *,
    key_fields: Tuple[str, ...] = ("market", "symbol"),
) -> List[Dict[str, Any]]:
    """Keep one row per key; prefer lower venue_priority index.

    Listings without live price and whale prints are left untouched by callers.
    """
    best: Dict[Any, Dict[str, Any]] = {}
    order: List[Any] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        key_parts = []
        skip = False
        for f in key_fields:
            v = row.get(f)
            if f == "symbol" and not v:
                skip = True
                break
            key_parts.append(v)
        if skip:
            continue
        key = tuple(key_parts)
        cur = best.get(key)
        if cur is None:
            best[key] = row
            order.append(key)
            continue
        pri_new = venue_priority.get(str(row.get("venue") or ""), 10_000)
        pri_old = venue_priority.get(str(cur.get("venue") or ""), 10_000)
        if pri_new < pri_old:
            best[key] = row
        elif pri_new == pri_old:
            # Same venue priority: prefer higher quote_volume_i
            nv = int(row.get("quote_volume_i") or 0)
            ov = int(cur.get("quote_volume_i") or 0)
            if nv > ov:
                best[key] = row
    return [best[k] for k in order if k in best]


def dedupe_stats(
    before: int, after: int
) -> Dict[str, int]:
    return {"before": before, "after": after, "dropped": max(0, before - after)}

"""Annotate snapshot sector rows with fresh native WS last trades (additive).

Never replaces ``price_i`` / score fields — HTTP poll remains source of truth.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple


def _norm_sym(raw: Any) -> str:
    s = str(raw or "").strip().upper()
    return s.replace("-", "").replace("_", "").replace("/", "")


def annotate_snapshot_rows(
    snap: Dict[str, Any],
    overlay: Dict[str, Any],
    *,
    max_age_ms: int = 15_000,
) -> Tuple[Dict[str, Any], int]:
    """Attach ``ws_*`` fields onto matching sector rows. Returns (snap, n_patched)."""
    last = overlay.get("last_by_symbol") if isinstance(overlay, dict) else None
    if not isinstance(last, dict) or not last:
        return snap, 0
    by_norm: Dict[str, Dict[str, Any]] = {}
    for k, v in last.items():
        if isinstance(v, dict):
            by_norm[_norm_sym(k)] = v
    if not by_norm:
        return snap, 0

    sectors = snap.get("sectors")
    if not isinstance(sectors, dict):
        return snap, 0

    out_sectors: Dict[str, Any] = {}
    patched = 0
    for name, rows in sectors.items():
        if not isinstance(rows, list):
            out_sectors[name] = rows
            continue
        new_rows = []
        for row in rows:
            if not isinstance(row, dict):
                new_rows.append(row)
                continue
            hit = by_norm.get(_norm_sym(row.get("symbol")))
            if not hit:
                new_rows.append(row)
                continue
            age = hit.get("age_ms")
            try:
                age_i = int(age) if age is not None else None
            except (TypeError, ValueError):
                age_i = None
            if age_i is None or age_i > int(max_age_ms):
                new_rows.append(row)
                continue
            nr = dict(row)
            nr["ws_price_i"] = hit.get("price_i")
            nr["ws_qty_i"] = hit.get("qty_i")
            nr["ws_ts_ms"] = hit.get("ts_ms")
            nr["ws_age_ms"] = age_i
            nr["ws_venue"] = hit.get("venue")
            nr["ws_side"] = hit.get("side")
            # Honesty: score price untouched
            nr["ws_replaces_price_i"] = False
            new_rows.append(nr)
            patched += 1
        out_sectors[name] = new_rows

    out = dict(snap)
    out["sectors"] = out_sectors
    return out, patched

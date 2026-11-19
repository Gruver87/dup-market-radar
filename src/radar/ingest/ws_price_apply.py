"""Age-gated native WS last → universe ``price_i`` (explicit opt-in).

Default off. When enabled, HTTP ``price_i`` is preserved as ``price_i_http``
and ``price_source`` becomes ``native_ws``. Fail-closed: no inventing prices.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

_UNIVERSE_LIST_KEYS = (
    "spot",
    "futures",
    "listings",
    "memes",
    "stables",
    "whales",
)


def _norm_sym(raw: Any) -> str:
    s = str(raw or "").strip().upper()
    return s.replace("-", "").replace("_", "").replace("/", "")


def apply_ws_to_universe(
    universe: Dict[str, Any],
    overlay: Dict[str, Any],
    *,
    max_age_ms: int = 15_000,
) -> Tuple[Dict[str, Any], int]:
    """Overwrite fresh matching rows' price_i. Returns (universe, n_applied)."""
    if not isinstance(universe, dict):
        return universe, 0
    last = overlay.get("last_by_symbol") if isinstance(overlay, dict) else None
    if not isinstance(last, dict) or not last:
        return universe, 0
    by_norm: Dict[str, Dict[str, Any]] = {}
    for k, v in last.items():
        if isinstance(v, dict) and v.get("price_i") is not None:
            by_norm[_norm_sym(k)] = v
    if not by_norm:
        return universe, 0

    out = dict(universe)
    applied = 0
    for key in _UNIVERSE_LIST_KEYS:
        rows = out.get(key)
        if not isinstance(rows, list):
            continue
        new_rows: List[Any] = []
        for row in rows:
            if not isinstance(row, dict):
                new_rows.append(row)
                continue
            hit = by_norm.get(_norm_sym(row.get("symbol")))
            if not hit:
                new_rows.append(row)
                continue
            try:
                age_i = int(hit.get("age_ms")) if hit.get("age_ms") is not None else None
            except (TypeError, ValueError):
                age_i = None
            if age_i is None or age_i > int(max_age_ms):
                new_rows.append(row)
                continue
            try:
                ws_px = int(hit["price_i"])
            except (TypeError, ValueError):
                new_rows.append(row)
                continue
            nr = dict(row)
            if nr.get("price_i") is not None and "price_i_http" not in nr:
                nr["price_i_http"] = nr.get("price_i")
            nr["price_i"] = ws_px
            nr["price_source"] = "native_ws"
            nr["ws_age_ms"] = age_i
            nr["ws_venue"] = hit.get("venue")
            new_rows.append(nr)
            applied += 1
        out[key] = new_rows
    return out, applied

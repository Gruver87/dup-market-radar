"""Stamp additive WS bar metrics onto universe rows (HTTP short_window remains SoT when present)."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Set, Tuple

_UNIVERSE_LIST_KEYS = (
    "spot",
    "futures",
    "listings",
    "memes",
    "stables",
    "whales",
)

# Min trades in current WS bar before we trust it enough to skip HTTP kline.
_DENSE_TRADE_FLOOR = 3


def _norm_sym(raw: Any) -> str:
    s = str(raw or "").strip().upper()
    return s.replace("-", "").replace("_", "").replace("/", "")


def _dense(hit: Dict[str, Any], suf: str) -> bool:
    key = f"ws_bar_{suf}_trade_count"
    try:
        return int(hit.get(key) or hit.get("ws_bar_trade_count") or 0) >= _DENSE_TRADE_FLOOR
    except (TypeError, ValueError):
        return False


def stamp_ws_bar_metrics(
    universe: Dict[str, Any],
    bar_by_symbol: Dict[str, Dict[str, Any]],
    *,
    promote_to_mtf: bool = True,
) -> Tuple[Dict[str, Any], int, Set[str]]:
    """Merge bar metrics onto rows.

    When ``promote_to_mtf`` and HTTP 15m empty, dense WS 15m (else dense 5m)
    fills ``change_bps_15m`` + ``mtf_attached_at_ms`` so ensure skips HTTP kline.

    Returns (universe, n_stamped, skip_http_symbols).
    """
    skip: Set[str] = set()
    if not isinstance(universe, dict) or not bar_by_symbol:
        return universe, 0, skip
    by_norm = {_norm_sym(k): v for k, v in bar_by_symbol.items() if isinstance(v, dict)}
    if not by_norm:
        return universe, 0, skip
    out = dict(universe)
    n = 0
    now_ms = int(time.time() * 1000)
    for key in _UNIVERSE_LIST_KEYS:
        rows = out.get(key)
        if not isinstance(rows, list):
            continue
        new_rows: List[Any] = []
        for row in rows:
            if not isinstance(row, dict):
                new_rows.append(row)
                continue
            sym_norm = _norm_sym(row.get("symbol"))
            hit = by_norm.get(sym_norm)
            if not hit:
                new_rows.append(row)
                continue
            nr = dict(row)
            for mk, mv in hit.items():
                nr[mk] = mv

            # Soft fill change_bps_5m
            if nr.get("change_bps_5m") is None:
                if hit.get("change_bps_ws_5m") is not None:
                    nr["change_bps_5m"] = hit["change_bps_ws_5m"]
                    nr["change_bps_5m_source"] = "native_ws_bar_5m"
                elif hit.get("change_bps_ws_1m") is not None:
                    nr["change_bps_5m"] = hit["change_bps_ws_1m"]
                    nr["change_bps_5m_source"] = "native_ws_bar_proxy"

            # Promote into HTTP 15m slot so waterfall can skip GETs
            if promote_to_mtf and nr.get("change_bps_15m") is None:
                promoted = False
                if hit.get("change_bps_ws_15m") is not None and _dense(hit, "15m"):
                    nr["change_bps_15m"] = hit["change_bps_ws_15m"]
                    nr["change_bps_15m_source"] = "native_ws_bar_15m"
                    promoted = True
                elif hit.get("change_bps_ws_5m") is not None and _dense(hit, "5m"):
                    nr["change_bps_15m"] = hit["change_bps_ws_5m"]
                    nr["change_bps_15m_source"] = "native_ws_bar_5m_proxy"
                    promoted = True
                if promoted:
                    nr["mtf_attached_at_ms"] = now_ms
                    nr["mtf_source"] = "native_ws"
                    skip.add(sym_norm)

            # Soft fill change_bps_1h / 4h from dense WS bars (no shorter-TF proxy)
            if nr.get("change_bps_1h") is None:
                if hit.get("change_bps_ws_1h") is not None and _dense(hit, "1h"):
                    nr["change_bps_1h"] = hit["change_bps_ws_1h"]
                    nr["change_bps_1h_source"] = "native_ws_bar_1h"
                    if str(nr.get("mtf_source") or "") == "native_ws":
                        nr["htf_source"] = "native_ws_bar_1h"
            if nr.get("change_bps_4h") is None:
                if hit.get("change_bps_ws_4h") is not None and _dense(hit, "4h"):
                    nr["change_bps_4h"] = hit["change_bps_ws_4h"]
                    nr["change_bps_4h_source"] = "native_ws_bar_4h"
                    if str(nr.get("mtf_source") or "") == "native_ws":
                        prev_htf = str(nr.get("htf_source") or "")
                        if prev_htf.startswith("native_ws"):
                            nr["htf_source"] = "native_ws_bar_htf"
                        else:
                            nr["htf_source"] = "native_ws_bar_4h"

            new_rows.append(nr)
            n += 1
        out[key] = new_rows
    return out, n, skip

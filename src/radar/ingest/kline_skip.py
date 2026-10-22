"""Kline soft-denylist — skip symbols that already 400'd.

Per-scan set is cleared each ``run_scan``. Process-lifetime set keeps
client-refuse symbols (delisted / bad symbol) across warm cycles so Binance
does not get re-hit every scan (e.g. AKEUSDT 400).
"""

from __future__ import annotations

import threading
from typing import Any, Set, Tuple

_PERSIST_MAX = 1024


def _lock(http: Any) -> threading.Lock:
    lock = getattr(http, "_kline_skip_lock", None)
    if lock is None:
        lock = threading.Lock()
        http._kline_skip_lock = lock
    return lock


def init_kline_skip(http: Any) -> None:
    """Clear per-scan denylist; keep process-lifetime refuse set."""
    with _lock(http):
        http._kline_skip = set()
        if not hasattr(http, "_kline_skip_persist"):
            http._kline_skip_persist = set()


def kline_skip_has(http: Any, venue: str, symbol: str) -> bool:
    key = (str(venue), str(symbol))
    with _lock(http):
        if key in getattr(http, "_kline_skip", set()):
            return True
        return key in getattr(http, "_kline_skip_persist", set())


def kline_skip_bump(http: Any) -> None:
    """Honesty counter when a denylisted symbol is skipped (no GET)."""
    stats = getattr(http, "stats", None)
    if isinstance(stats, dict):
        stats["kline_skip_hits"] = int(stats.get("kline_skip_hits") or 0) + 1


def kline_skip_add(http: Any, venue: str, symbol: str) -> bool:
    """Add to per-scan + process-lifetime refuse sets.

    Returns True if this was a new process-lifetime entry (first refuse).
    """
    sym = str(symbol or "").strip()
    if not sym:
        return False
    key = (str(venue), sym)
    with _lock(http):
        skipped: Set[Tuple[str, str]] = getattr(http, "_kline_skip", None) or set()
        skipped.add(key)
        if len(skipped) > 512:
            skipped = set(list(skipped)[-256:])
        http._kline_skip = skipped

        persist: Set[Tuple[str, str]] = (
            getattr(http, "_kline_skip_persist", None) or set()
        )
        is_new = key not in persist
        persist.add(key)
        if len(persist) > _PERSIST_MAX:
            # Drop arbitrary oldest-ish half by list slice (set order unstable OK).
            persist = set(list(persist)[-(_PERSIST_MAX // 2) :])
        http._kline_skip_persist = persist
        return is_new


def is_client_refuse_exc(exc: BaseException) -> bool:
    """True for HTTP 4xx client errors (invalid symbol / delisted)."""
    text = str(exc)
    if "400" in text or "404" in text or "Bad Request" in text:
        return True
    code = getattr(getattr(exc, "response", None), "status_code", None)
    try:
        c = int(code) if code is not None else 0
    except (TypeError, ValueError):
        c = 0
    return 400 <= c < 500 and c != 429

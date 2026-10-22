"""Per-scan kline soft-denylist — skip symbols that already 400'd this scan."""

from __future__ import annotations

import threading
from typing import Any, Set, Tuple


def _lock(http: Any) -> threading.Lock:
    lock = getattr(http, "_kline_skip_lock", None)
    if lock is None:
        lock = threading.Lock()
        http._kline_skip_lock = lock
    return lock


def init_kline_skip(http: Any) -> None:
    with _lock(http):
        http._kline_skip = set()


def kline_skip_has(http: Any, venue: str, symbol: str) -> bool:
    key = (str(venue), str(symbol))
    with _lock(http):
        skipped: Set[Tuple[str, str]] = getattr(http, "_kline_skip", set())
        return key in skipped


def kline_skip_add(http: Any, venue: str, symbol: str) -> None:
    sym = str(symbol or "").strip()
    if not sym:
        return
    with _lock(http):
        skipped: Set[Tuple[str, str]] = getattr(http, "_kline_skip", None) or set()
        skipped.add((str(venue), sym))
        # Soft cap — refuse unbounded growth if a venue melts down.
        if len(skipped) > 512:
            skipped = set(list(skipped)[-256:])
        http._kline_skip = skipped


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

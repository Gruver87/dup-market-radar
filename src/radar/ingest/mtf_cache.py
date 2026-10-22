"""Process-lifetime MTF reuse cache (0.2.87).

Fresh CEX ingest rebuilds row dicts without short-window fields, so
``mtf_reuse_ttl_ms`` alone never fired across scans. This cache hydrates
stamped MTF onto matching symbols before ensure, then remembers after attach.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

_MTF_KEYS = (
    "change_bps_15m",
    "change_bps_1h",
    "change_bps_4h",
    "change_pct_15m",
    "change_pct_1h",
    "change_pct_4h",
    "vol_vs_median_bps",
    "mtf_attached_at_ms",
)

_MAX_ENTRIES = 768


class MtfReuseCache:
    """Thread-safe bounded last-good MTF by market+symbol."""

    def __init__(self, *, max_entries: int = _MAX_ENTRIES) -> None:
        self._max = max(32, int(max_entries))
        self._lock = threading.Lock()
        self._store: Dict[str, Dict[str, Any]] = {}
        self.stats = {"hydrate_hits": 0, "remembered": 0, "evictions": 0}

    def reset(self) -> None:
        with self._lock:
            self._store.clear()
            self.stats = {"hydrate_hits": 0, "remembered": 0, "evictions": 0}

    @staticmethod
    def _key(market: str, symbol: str) -> str:
        return f"{str(market).lower()}:{str(symbol).upper()}"

    def hydrate_rows(
        self,
        rows: List[Dict[str, Any]],
        *,
        market: str,
        ttl_ms: int,
    ) -> int:
        """Copy fresh cached MTF onto rows missing 15m. Returns hit count."""
        ttl = max(0, int(ttl_ms or 0))
        if ttl <= 0 or not rows:
            return 0
        now = int(time.time() * 1000)
        hits = 0
        with self._lock:
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if (
                    row.get("change_bps_15m") is not None
                    or row.get("change_pct_15m") is not None
                ):
                    continue
                sym = str(row.get("symbol") or "").strip()
                if not sym:
                    continue
                packed = self._store.get(self._key(market, sym))
                if not packed:
                    continue
                at = packed.get("mtf_attached_at_ms")
                try:
                    age = now - int(at)
                except (TypeError, ValueError):
                    continue
                if age < 0 or age >= ttl:
                    continue
                for k in _MTF_KEYS:
                    if k in packed:
                        row[k] = packed[k]
                hits += 1
            self.stats["hydrate_hits"] += hits
        return hits

    def remember_rows(self, rows: List[Dict[str, Any]], *, market: str) -> int:
        """Store rows that carry stamped 15m MTF. Returns stored count."""
        if not rows:
            return 0
        stored = 0
        with self._lock:
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if (
                    row.get("change_bps_15m") is None
                    and row.get("change_pct_15m") is None
                ):
                    continue
                sym = str(row.get("symbol") or "").strip()
                if not sym:
                    continue
                at = row.get("mtf_attached_at_ms")
                if at is None:
                    at = int(time.time() * 1000)
                    row["mtf_attached_at_ms"] = at
                packed = {k: row[k] for k in _MTF_KEYS if k in row}
                packed["mtf_attached_at_ms"] = int(at)
                self._store[self._key(market, sym)] = packed
                stored += 1
            while len(self._store) > self._max:
                # Drop arbitrary oldest by stamp.
                oldest = min(
                    self._store.items(),
                    key=lambda kv: int(kv[1].get("mtf_attached_at_ms") or 0),
                )[0]
                self._store.pop(oldest, None)
                self.stats["evictions"] += 1
            self.stats["remembered"] += stored
        return stored


_CACHE: Optional[MtfReuseCache] = None
_CACHE_LOCK = threading.Lock()


def mtf_cache() -> MtfReuseCache:
    global _CACHE
    with _CACHE_LOCK:
        if _CACHE is None:
            _CACHE = MtfReuseCache()
        return _CACHE

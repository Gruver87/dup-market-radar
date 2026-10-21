"""Sector ingest cadence — honor sectors.*.poll_sec for fetch groups.

Groups (honest bundling):
- cex: spot + futures + listings + whales (venue dumps are atomic)
- memes: DexScreener
- stables: CoinGecko

Whales poll_sec alone cannot force a separate HTTP path — it rides with cex.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional, Set

# Fetch groups ↔ config sector keys used for poll_sec
GROUP_SECTORS: Dict[str, tuple[str, ...]] = {
    "cex": ("spot_alts", "futures", "listings"),  # whales bundled; not a driver
    "memes": ("memes",),
    "stables": ("stables",),
}

DEFAULT_POLL_SEC = 60
MIN_POLL_SEC = 5
MAX_POLL_SEC = 3600


def _clamp_poll_sec(raw: Any) -> int:
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_POLL_SEC
    return max(MIN_POLL_SEC, min(MAX_POLL_SEC, v))


def sector_poll_sec(cfg: Dict[str, Any], sector: str) -> int:
    sec = (cfg.get("sectors") or {}).get(sector) or {}
    if sec.get("poll_sec") is None:
        return DEFAULT_POLL_SEC
    return _clamp_poll_sec(sec.get("poll_sec"))


def group_poll_sec(cfg: Dict[str, Any], group: str) -> int:
    """Effective poll for a fetch group = min of driver sectors."""
    keys = GROUP_SECTORS.get(group) or ()
    if not keys:
        return DEFAULT_POLL_SEC
    return min(sector_poll_sec(cfg, k) for k in keys)


def group_poll_ms(cfg: Dict[str, Any], group: str) -> int:
    return group_poll_sec(cfg, group) * 1000


def effective_snapshot_refresh_ms(cfg: Dict[str, Any], base_ms: int) -> int:
    """Outer snapshot cache TTL — allow memes/cex cadence to wake earlier."""
    base = max(1000, int(base_ms))
    polls = [group_poll_ms(cfg, g) for g in GROUP_SECTORS]
    return max(5000, min(base, min(polls)))


class SectorPollState:
    """Thread-safe last-fetch timestamps + cached universe slices."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._fetched_at_ms: Dict[str, int] = {}
        self._spot: List[Dict[str, Any]] = []
        self._futures: List[Dict[str, Any]] = []
        self._listings: List[Dict[str, Any]] = []
        self._whales: List[Dict[str, Any]] = []
        self._memes: List[Dict[str, Any]] = []
        self._stables: List[Dict[str, Any]] = []

    def reset(self) -> None:
        with self._lock:
            self._fetched_at_ms.clear()
            self._spot = []
            self._futures = []
            self._listings = []
            self._whales = []
            self._memes = []
            self._stables = []

    def due_groups(
        self, cfg: Dict[str, Any], *, now_ms: Optional[int] = None, force: bool = False
    ) -> Set[str]:
        now = int(now_ms if now_ms is not None else time.time() * 1000)
        due: Set[str] = set()
        with self._lock:
            for group in GROUP_SECTORS:
                if force:
                    due.add(group)
                    continue
                last = self._fetched_at_ms.get(group)
                if last is None:
                    due.add(group)
                    continue
                if now - last >= group_poll_ms(cfg, group):
                    due.add(group)
        return due

    def status(self, cfg: Dict[str, Any], *, now_ms: Optional[int] = None) -> Dict[str, Any]:
        now = int(now_ms if now_ms is not None else time.time() * 1000)
        out: Dict[str, Any] = {}
        with self._lock:
            for group in GROUP_SECTORS:
                last = self._fetched_at_ms.get(group)
                poll_ms = group_poll_ms(cfg, group)
                age = None if last is None else max(0, now - last)
                out[group] = {
                    "poll_sec": poll_ms // 1000,
                    "age_ms": age,
                    "due": last is None or (age is not None and age >= poll_ms),
                    "sectors": list(GROUP_SECTORS[group]),
                }
            out["whales_note"] = "bundled_with_cex"
        return out

    def mark_fetched(self, group: str, *, now_ms: Optional[int] = None) -> None:
        with self._lock:
            self._fetched_at_ms[group] = int(
                now_ms if now_ms is not None else time.time() * 1000
            )

    def store_cex(
        self,
        *,
        spot: List[Dict[str, Any]],
        futures: List[Dict[str, Any]],
        listings: List[Dict[str, Any]],
        whales: List[Dict[str, Any]],
        now_ms: Optional[int] = None,
    ) -> None:
        with self._lock:
            self._spot = list(spot)
            self._futures = list(futures)
            self._listings = list(listings)
            self._whales = list(whales)
            self._fetched_at_ms["cex"] = int(
                now_ms if now_ms is not None else time.time() * 1000
            )

    def store_memes(
        self, memes: List[Dict[str, Any]], *, now_ms: Optional[int] = None
    ) -> None:
        with self._lock:
            self._memes = list(memes)
            self._fetched_at_ms["memes"] = int(
                now_ms if now_ms is not None else time.time() * 1000
            )

    def store_stables(
        self, stables: List[Dict[str, Any]], *, now_ms: Optional[int] = None
    ) -> None:
        with self._lock:
            self._stables = list(stables)
            self._fetched_at_ms["stables"] = int(
                now_ms if now_ms is not None else time.time() * 1000
            )

    def cached_slices(self) -> Dict[str, List[Dict[str, Any]]]:
        with self._lock:
            return {
                "spot": list(self._spot),
                "futures": list(self._futures),
                "listings": list(self._listings),
                "whales": list(self._whales),
                "memes": list(self._memes),
                "stables": list(self._stables),
            }


_STATE = SectorPollState()


def poll_state() -> SectorPollState:
    return _STATE

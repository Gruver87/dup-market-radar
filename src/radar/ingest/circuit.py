"""Per-venue circuit breaker — soft-open after consecutive failures."""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Dict, Optional

from radar.secrets_redact import redact_text

logger = logging.getLogger("radar.circuit")

_VENUE_RE = re.compile(r"[^A-Za-z0-9_.\-]")
MAX_VENUE_LEN = 64
MAX_REASON_LEN = 120
# Soft cap tracked venues — refuse new keys at ceiling (known venues still update).
MAX_TRACKED_VENUES = 64


def _normalize_venue(venue: str) -> str:
    s = _VENUE_RE.sub("_", str(venue or "").strip())
    s = s.strip("._-")[:MAX_VENUE_LEN]
    return s or "unknown"


class VenueCircuitBreaker:
    def __init__(self, fail_threshold: int = 3, open_ms: int = 60_000) -> None:
        self._fail_threshold = max(1, int(fail_threshold))
        self._open_ms = max(1, int(open_ms))
        self._lock = threading.Lock()
        self._fails: Dict[str, int] = {}
        self._open_until_ms: Dict[str, int] = {}

    def allow(self, venue: str) -> bool:
        venue = _normalize_venue(venue)
        with self._lock:
            until = int(self._open_until_ms.get(venue) or 0)
            now = int(time.time() * 1000)
            if until > now:
                return False
            if until and until <= now:
                # half-open: clear open window, keep fail count for next trip
                self._open_until_ms.pop(venue, None)
            return True

    def record_ok(self, venue: str) -> None:
        venue = _normalize_venue(venue)
        with self._lock:
            if venue not in self._fails and len(self._fails) >= MAX_TRACKED_VENUES:
                return
            self._fails[venue] = 0
            self._open_until_ms.pop(venue, None)

    def record_fail(self, venue: str, reason: str = "") -> None:
        venue = _normalize_venue(venue)
        safe_reason = ""
        if reason:
            safe_reason = redact_text(reason)[:MAX_REASON_LEN]
        with self._lock:
            if venue not in self._fails and len(self._fails) >= MAX_TRACKED_VENUES:
                # Soft refuse growth — do not open circuit for untracked junk keys
                return
            n = int(self._fails.get(venue) or 0) + 1
            self._fails[venue] = n
            if n >= self._fail_threshold:
                until = int(time.time() * 1000) + self._open_ms
                self._open_until_ms[venue] = until
                logger.warning(
                    "circuit OPEN venue=%s fails=%s open_ms=%s reason=%s",
                    venue,
                    n,
                    self._open_ms,
                    safe_reason,
                )

    def status(self) -> Dict[str, Any]:
        with self._lock:
            now = int(time.time() * 1000)
            open_venues = {
                v: max(0, until - now)
                for v, until in list(self._open_until_ms.items())[:MAX_TRACKED_VENUES]
                if until > now
            }
            fails = dict(list(self._fails.items())[:MAX_TRACKED_VENUES])
            return {
                "fail_threshold": self._fail_threshold,
                "open_ms": self._open_ms,
                "fails": fails,
                "open_remaining_ms": open_venues,
                "tracked_venues": len(self._fails),
                "max_tracked_venues": MAX_TRACKED_VENUES,
            }


# Process-wide breaker shared across scan workers
_BREAKER: Optional[VenueCircuitBreaker] = None
_BREAKER_LOCK = threading.Lock()


def get_breaker(cfg: Optional[Dict[str, Any]] = None) -> VenueCircuitBreaker:
    global _BREAKER
    with _BREAKER_LOCK:
        ingest = (cfg or {}).get("ingest") or {}
        threshold = int(ingest.get("circuit_fail_threshold") or 3)
        open_ms = int(ingest.get("circuit_open_ms") or 60_000)
        if _BREAKER is None:
            _BREAKER = VenueCircuitBreaker(threshold, open_ms)
        else:
            _BREAKER._fail_threshold = max(1, threshold)
            _BREAKER._open_ms = max(1, open_ms)
        return _BREAKER


def reset_breaker_for_tests() -> None:
    global _BREAKER
    with _BREAKER_LOCK:
        _BREAKER = None

"""In-process sliding-window rate limiter (trade endpoints). Soft refuse = 429."""

from __future__ import annotations

import math
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple

# Bound identity string size (XFF / Host abuse).
MAX_KEY_LEN = 64
# Soft cap on distinct keys — prune empty queues; refuse new keys at ceiling.
DEFAULT_MAX_KEYS = 10_000


class RateLimiter:
    def __init__(
        self,
        max_per_window: int = 20,
        window_ms: int = 60_000,
        *,
        max_keys: int = DEFAULT_MAX_KEYS,
    ) -> None:
        self._max = max(1, int(max_per_window))
        self._window_ms = max(1, int(window_ms))
        self._max_keys = max(16, int(max_keys))
        self._lock = threading.Lock()
        self._hits: Dict[str, Deque[int]] = defaultdict(deque)
        self.stats = {"allowed": 0, "refused": 0, "key_cap_refused": 0}

    @staticmethod
    def normalize_key(key: str) -> str:
        s = str(key or "").strip()
        if not s:
            return "unknown"
        # Drop control chars; keep printable for ops readability.
        s = "".join(c for c in s if 32 <= ord(c) < 127)
        if not s:
            return "unknown"
        return s[:MAX_KEY_LEN]

    def allow(self, key: str) -> bool:
        ok, _retry = self.check(key)
        return ok

    def check(self, key: str) -> Tuple[bool, int]:
        """Return (allowed, retry_after_sec). retry_after_sec=0 when allowed."""
        key = self.normalize_key(key)
        now = int(time.time() * 1000)
        cutoff = now - self._window_ms
        with self._lock:
            self._prune_empty_unlocked(cutoff)
            if key not in self._hits and len(self._hits) >= self._max_keys:
                self.stats["refused"] += 1
                self.stats["key_cap_refused"] += 1
                return False, 1
            q = self._hits[key]
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= self._max:
                self.stats["refused"] += 1
                oldest = q[0] if q else now
                wait_ms = max(0, (oldest + self._window_ms) - now)
                return False, max(1, int(math.ceil(wait_ms / 1000.0)))
            q.append(now)
            self.stats["allowed"] += 1
            return True, 0

    def _prune_empty_unlocked(self, cutoff: int) -> None:
        # Drop keys whose entire window has expired.
        for k in list(self._hits.keys()):
            q = self._hits[k]
            while q and q[0] < cutoff:
                q.popleft()
            if not q:
                del self._hits[k]

    def status(self) -> Dict[str, int]:
        with self._lock:
            return {
                "max_per_window": self._max,
                "window_ms": self._window_ms,
                "max_keys": self._max_keys,
                "tracked_keys": len(self._hits),
                **dict(self.stats),
            }

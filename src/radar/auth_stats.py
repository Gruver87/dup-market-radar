"""In-process API-key auth counters for /api/metrics."""

from __future__ import annotations

import threading
from typing import Any, Dict


_ALLOWED = frozenset({"auth_ok", "auth_fail"})


class AuthStats:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.auth_ok = 0
        self.auth_fail = 0

    def inc(self, name: str, n: int = 1) -> None:
        if name not in _ALLOWED:
            return
        with self._lock:
            cur = int(getattr(self, name, 0))
            setattr(self, name, cur + int(n))

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "auth_ok": self.auth_ok,
                "auth_fail": self.auth_fail,
            }


AUTH_STATS = AuthStats()

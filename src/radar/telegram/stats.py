"""In-process Telegram counters for /api/metrics."""

from __future__ import annotations

import threading
from typing import Any, Dict


_ALLOWED = frozenset(
    {
        "alerts_sent",
        "alerts_skipped_stale",
        "alerts_skipped_empty",
        "alerts_skipped_degraded",
        "watch_add_fail",
        "cmd_rate_refused",
    }
)


class TelegramStats:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.alerts_sent = 0
        self.alerts_skipped_stale = 0
        self.alerts_skipped_empty = 0
        self.alerts_skipped_degraded = 0
        self.watch_add_fail = 0
        self.cmd_rate_refused = 0

    def inc(self, name: str, n: int = 1) -> None:
        if name not in _ALLOWED:
            return
        with self._lock:
            cur = int(getattr(self, name, 0))
            setattr(self, name, cur + int(n))

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "alerts_sent": self.alerts_sent,
                "alerts_skipped_stale": self.alerts_skipped_stale,
                "alerts_skipped_empty": self.alerts_skipped_empty,
                "alerts_skipped_degraded": self.alerts_skipped_degraded,
                "watch_add_fail": self.watch_add_fail,
                "cmd_rate_refused": self.cmd_rate_refused,
            }


TG_STATS = TelegramStats()

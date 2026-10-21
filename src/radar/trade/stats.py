"""In-process trade counters for /api/metrics."""

from __future__ import annotations

import threading
from typing import Any, Dict

_ALLOWED = frozenset(
    {
        "place_ok",
        "place_fail",
        "place_preview",
        "duplicate_oid",
        "fills_ok",
        "fills_fail",
        "sell_clamped",
    }
)


class TradeStats:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.place_ok = 0
        self.place_fail = 0
        self.place_preview = 0
        self.duplicate_oid = 0
        self.fills_ok = 0
        self.fills_fail = 0
        self.sell_clamped = 0

    def inc(self, name: str, n: int = 1) -> None:
        if name not in _ALLOWED:
            return
        with self._lock:
            cur = int(getattr(self, name, 0))
            setattr(self, name, cur + int(n))

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "place_ok": self.place_ok,
                "place_fail": self.place_fail,
                "place_preview": self.place_preview,
                "duplicate_oid": self.duplicate_oid,
                "fills_ok": self.fills_ok,
                "fills_fail": self.fills_fail,
                "sell_clamped": self.sell_clamped,
            }


TRADE_STATS = TradeStats()

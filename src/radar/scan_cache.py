"""Single-flight snapshot cache — one scan at a time, waiters share result."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("radar.scan_cache")


class SnapshotCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._snap: Optional[Dict[str, Any]] = None
        self._ts_ms: int = 0
        self._inflight: bool = False
        self.stats: Dict[str, int] = {
            "hits": 0,
            "misses": 0,
            "single_flight_waits": 0,
            "scans": 0,
            "scan_errors": 0,
        }

    def peek(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._snap

    def get(
        self,
        *,
        force: bool,
        refresh_ms: int,
        scan_fn: Callable[[], Dict[str, Any]],
        wait_ms: int = 180_000,
    ) -> Dict[str, Any]:
        refresh_ms = max(1, int(refresh_ms))
        deadline = int(time.time() * 1000) + int(wait_ms)

        with self._cond:
            now = int(time.time() * 1000)
            if (
                not force
                and self._snap is not None
                and now - self._ts_ms < refresh_ms
            ):
                self.stats["hits"] += 1
                return self._snap

            if self._inflight:
                self.stats["single_flight_waits"] += 1
                while self._inflight:
                    remaining = deadline - int(time.time() * 1000)
                    if remaining <= 0:
                        logger.warning("single-flight wait timeout")
                        break
                    self._cond.wait(timeout=min(1.0, remaining / 1000.0))
                # Coalesce onto leader result (force and non-force waiters)
                if self._snap is not None:
                    self.stats["hits"] += 1
                    return self._snap

            self._inflight = True
            self.stats["misses"] += 1

        try:
            snap = scan_fn()
            self.stats["scans"] += 1
        except Exception as exc:
            from radar.secrets_redact import redact_exc

            self.stats["scan_errors"] += 1
            # Message scrubbed; avoid raw exc in format args before formatter runs
            # No exc_info — stack frames can embed URLs/headers before RedactingFormatter
            logger.error("scan failed: %s", redact_exc(exc, max_len=256))
            with self._cond:
                self._inflight = False
                self._cond.notify_all()
            raise

        with self._cond:
            self._snap = snap
            self._ts_ms = int(time.time() * 1000)
            self._inflight = False
            self._cond.notify_all()
        # Notify live subscribers outside the lock (WebSocket hub).
        try:
            from radar.api.ws_hub import HUB

            HUB.publish(
                {
                    "type": "snapshot",
                    "ok": bool(snap.get("ok")) if isinstance(snap, dict) else False,
                    "generated_at_ms": (
                        snap.get("generated_at_ms") if isinstance(snap, dict) else None
                    ),
                    "mtf_ensure_ok": (
                        snap.get("mtf_ensure_ok") if isinstance(snap, dict) else None
                    ),
                    "universe_size": (
                        snap.get("universe_size") if isinstance(snap, dict) else None
                    ),
                }
            )
        except Exception:  # noqa: BLE001
            pass
        return snap

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "has_snapshot": self._snap is not None,
                "age_ms": (
                    int(time.time() * 1000) - self._ts_ms if self._snap is not None else None
                ),
                "inflight": self._inflight,
                "stats": dict(self.stats),
            }

"""In-process WebSocket fan-out for snapshot / alert / trade ticks."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Dict, Optional, Set

from radar.secrets_redact import redact_exc

logger = logging.getLogger("radar.api.ws")


class WsHub:
    def __init__(self, *, max_queue: int = 32) -> None:
        self._max_queue = max(4, int(max_queue))
        self._lock = threading.Lock()
        self._subs: Set[asyncio.Queue] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._published = 0
        self._dropped = 0

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        with self._lock:
            self._loop = loop

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "subscribers": len(self._subs),
                "published": self._published,
                "dropped": self._dropped,
                "max_queue": self._max_queue,
            }

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._max_queue)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs.discard(q)

    def close_all(self) -> None:
        """Wake waiters and clear subs — used on process shutdown (Ctrl+C)."""
        with self._lock:
            subs = list(self._subs)
            self._subs.clear()
        for q in subs:
            try:
                q.put_nowait({"type": "_shutdown"})
            except Exception:  # noqa: BLE001
                pass

    def publish(self, event: Dict[str, Any]) -> None:
        """Thread-safe publish; schedules onto bound event loop when needed."""
        payload = dict(event)
        payload.setdefault("at_ms", int(time.time() * 1000))
        with self._lock:
            loop = self._loop
            subs = list(self._subs)
            self._published += 1
        if not subs:
            return

        def _fanout() -> None:
            for q in subs:
                try:
                    q.put_nowait(payload)
                except asyncio.QueueFull:
                    # Drop oldest then retry once — live stream prefers freshness
                    try:
                        _ = q.get_nowait()
                        q.put_nowait(payload)
                        with self._lock:
                            self._dropped += 1
                    except Exception:  # noqa: BLE001
                        with self._lock:
                            self._dropped += 1
                except Exception as exc:  # noqa: BLE001
                    logger.debug("ws fanout: %s", redact_exc(exc, max_len=64))

        if loop is None:
            _fanout()
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            _fanout()
        else:
            try:
                loop.call_soon_threadsafe(_fanout)
            except Exception as exc:  # noqa: BLE001
                logger.warning("ws publish schedule failed: %s", redact_exc(exc, max_len=64))


HUB = WsHub()

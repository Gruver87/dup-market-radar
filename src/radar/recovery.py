"""Process self-recovery — watchdog threads + background scan after outages.

Invariant: recovery restarts loops and refreshes snapshots; it does **not**
enable auto-trade, bypass degraded `ok=false`, or silence venue circuits.
Trade paths stay fail-closed while analysis is unhealthy.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from radar.config import load_config
from radar.secrets_redact import redact_exc

logger = logging.getLogger("radar.recovery")

# Soft caps — refuse absurd operator config without crashing.
_MIN_TICK_SEC = 5
_MAX_TICK_SEC = 300
_MAX_RESTARTS_PER_HOUR = 60
_MAX_BACKOFF_MS = 120_000


class RecoverySupervisor:
    """Daemon loop: revive dead workers, keep scans warm, detect network return."""

    def __init__(
        self,
        *,
        snapshot_fn: Callable[[], Dict[str, Any]],
        ensure_auto: Callable[[], bool],
        ensure_telegram: Callable[[], bool],
        auto_alive: Callable[[], bool],
        telegram_wanted: Callable[[], bool],
        telegram_alive: Callable[[], bool],
        on_network_recovered: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        self._snapshot_fn = snapshot_fn
        self._ensure_auto = ensure_auto
        self._ensure_telegram = ensure_telegram
        self._auto_alive = auto_alive
        self._telegram_wanted = telegram_wanted
        self._telegram_alive = telegram_alive
        self._on_network_recovered = on_network_recovered
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._stats: Dict[str, int] = {
            "ticks": 0,
            "background_scans": 0,
            "scan_ok": 0,
            "scan_fail": 0,
            "auto_restarts": 0,
            "telegram_restarts": 0,
            "network_recoveries": 0,
            "restart_refused_budget": 0,
        }
        self._consecutive_scan_fails = 0
        self._last_scan_ok_ms: Optional[int] = None
        self._last_scan_fail_ms: Optional[int] = None
        self._last_recovery_ms: Optional[int] = None
        self._last_error: Optional[str] = None
        self._restart_times_ms: List[int] = []
        self._events: List[Dict[str, Any]] = []  # bounded recent

    def start(self) -> None:
        cfg = load_config().get("recovery") or {}
        if cfg.get("enabled") is False:
            logger.info("recovery.enabled=false — supervisor skip")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="radar-recovery", daemon=True
        )
        self._thread.start()
        logger.info("recovery supervisor started")

    def stop(self, *, join_timeout_sec: float = 5.0) -> None:
        self._stop.set()
        thr = self._thread
        if thr is not None and thr.is_alive():
            thr.join(timeout=max(0.1, float(join_timeout_sec)))
            if thr.is_alive():
                logger.warning("recovery thread still alive after join")
        self._thread = None

    def status(self) -> Dict[str, Any]:
        cfg = load_config().get("recovery") or {}
        with self._lock:
            return {
                "enabled": cfg.get("enabled") is not False,
                "running": bool(self._thread and self._thread.is_alive()),
                "tick_sec": self._tick_sec(cfg),
                "background_scan": bool(cfg.get("background_scan", True)),
                "auto_alive": bool(self._auto_alive()),
                "telegram_wanted": bool(self._telegram_wanted()),
                "telegram_alive": bool(self._telegram_alive()),
                "consecutive_scan_fails": self._consecutive_scan_fails,
                "last_scan_ok_ms": self._last_scan_ok_ms,
                "last_scan_fail_ms": self._last_scan_fail_ms,
                "last_recovery_ms": self._last_recovery_ms,
                "last_error": self._last_error,
                "stats": dict(self._stats),
                "recent_events": list(self._events[-16:]),
            }

    def _tick_sec(self, cfg: Dict[str, Any]) -> int:
        raw = int(cfg.get("tick_sec") or 15)
        return max(_MIN_TICK_SEC, min(_MAX_TICK_SEC, raw))

    def _loop(self) -> None:
        while not self._stop.is_set():
            cfg = load_config().get("recovery") or {}
            if cfg.get("enabled") is False:
                self._stop.wait(self._tick_sec(cfg))
                continue
            try:
                self._tick(cfg)
            except Exception as exc:  # noqa: BLE001
                err = redact_exc(exc, max_len=256)
                with self._lock:
                    self._last_error = err
                logger.error("recovery tick failed: %s", err)
            self._stop.wait(self._tick_sec(cfg))

    def _tick(self, cfg: Dict[str, Any]) -> None:
        with self._lock:
            self._stats["ticks"] += 1

        if bool(cfg.get("restart_auto_thread", True)):
            self._maybe_restart_auto(cfg)
        if bool(cfg.get("restart_telegram", True)):
            self._maybe_restart_telegram(cfg)

        if bool(cfg.get("background_scan", True)):
            self._maybe_background_scan(cfg)

    def _restart_budget_ok(self, cfg: Dict[str, Any]) -> bool:
        now = int(time.time() * 1000)
        window_ms = 3_600_000
        cap = int(cfg.get("max_restarts_per_hour") or 20)
        cap = max(1, min(_MAX_RESTARTS_PER_HOUR, cap))
        with self._lock:
            self._restart_times_ms = [
                t for t in self._restart_times_ms if now - t < window_ms
            ]
            if len(self._restart_times_ms) >= cap:
                self._stats["restart_refused_budget"] += 1
                return False
            return True

    def _note_restart(self, kind: str) -> None:
        now = int(time.time() * 1000)
        with self._lock:
            self._restart_times_ms.append(now)
            self._events.append({"at_ms": now, "kind": kind})
            if len(self._events) > 64:
                self._events = self._events[-64:]

    def _backoff_wait(self, cfg: Dict[str, Any]) -> None:
        ms = int(cfg.get("restart_backoff_ms") or 2000)
        ms = max(100, min(_MAX_BACKOFF_MS, ms))
        self._stop.wait(ms / 1000.0)

    def _maybe_restart_auto(self, cfg: Dict[str, Any]) -> None:
        if self._auto_alive():
            return
        if not self._restart_budget_ok(cfg):
            logger.warning("auto thread dead — restart budget exhausted")
            return
        logger.warning("auto thread dead — restarting")
        self._backoff_wait(cfg)
        try:
            if self._ensure_auto():
                with self._lock:
                    self._stats["auto_restarts"] += 1
                self._note_restart("auto_restart")
        except Exception as exc:  # noqa: BLE001
            err = redact_exc(exc, max_len=128)
            with self._lock:
                self._last_error = err
            logger.error("auto restart failed: %s", err)

    def _maybe_restart_telegram(self, cfg: Dict[str, Any]) -> None:
        if not self._telegram_wanted():
            return
        if self._telegram_alive():
            return
        if not self._restart_budget_ok(cfg):
            logger.warning("telegram threads dead — restart budget exhausted")
            return
        logger.warning("telegram threads dead — restarting")
        self._backoff_wait(cfg)
        try:
            if self._ensure_telegram():
                with self._lock:
                    self._stats["telegram_restarts"] += 1
                self._note_restart("telegram_restart")
        except Exception as exc:  # noqa: BLE001
            err = redact_exc(exc, max_len=128)
            with self._lock:
                self._last_error = err
            logger.error("telegram restart failed: %s", err)

    def _maybe_background_scan(self, cfg: Dict[str, Any]) -> None:
        # Respect global snapshot_refresh_ms as minimum cadence.
        root = load_config()
        refresh_ms = int(root.get("snapshot_refresh_ms") or 45_000)
        refresh_ms = max(5_000, min(600_000, refresh_ms))
        now = int(time.time() * 1000)
        with self._lock:
            last_ok = self._last_scan_ok_ms
            last_fail = self._last_scan_fail_ms
        last_any = max(last_ok or 0, last_fail or 0)
        # After failures, back off but keep probing (network return).
        fails = self._consecutive_scan_fails
        if fails > 0:
            # 5s, 10s, 20s… capped at refresh_ms
            gap = min(refresh_ms, 5_000 * (2 ** min(fails, 4)))
        else:
            gap = refresh_ms
        if last_any and (now - last_any) < gap:
            return

        with self._lock:
            self._stats["background_scans"] += 1
        try:
            snap = self._snapshot_fn() or {}
            ok = bool(snap.get("ok")) if isinstance(snap, dict) else False
            now2 = int(time.time() * 1000)
            with self._lock:
                if ok:
                    prev_fails = self._consecutive_scan_fails
                    self._consecutive_scan_fails = 0
                    self._last_scan_ok_ms = now2
                    self._stats["scan_ok"] += 1
                    self._last_error = None
                    recovered = prev_fails >= 2
                else:
                    self._consecutive_scan_fails += 1
                    self._last_scan_fail_ms = now2
                    self._stats["scan_fail"] += 1
                    recovered = False
            if recovered:
                self._emit_network_recovered(prev_fails, snap)
        except Exception as exc:  # noqa: BLE001
            err = redact_exc(exc, max_len=256)
            now2 = int(time.time() * 1000)
            with self._lock:
                self._consecutive_scan_fails += 1
                self._last_scan_fail_ms = now2
                self._stats["scan_fail"] += 1
                self._last_error = err
            logger.warning("background scan failed: %s", err)

    def _emit_network_recovered(self, prev_fails: int, snap: Dict[str, Any]) -> None:
        now = int(time.time() * 1000)
        payload = {
            "at_ms": now,
            "prev_consecutive_fails": int(prev_fails),
            "ok": bool(snap.get("ok")),
            "mtf_ensure_ok": snap.get("mtf_ensure_ok"),
            "universe_size": snap.get("universe_size"),
        }
        with self._lock:
            self._stats["network_recoveries"] += 1
            self._last_recovery_ms = now
            self._events.append({"at_ms": now, "kind": "network_recovered", **payload})
            if len(self._events) > 64:
                self._events = self._events[-64:]
        logger.info(
            "network recovered after %s consecutive scan fails ok=%s mtf=%s",
            prev_fails,
            payload.get("ok"),
            payload.get("mtf_ensure_ok"),
        )
        cfg = load_config().get("recovery") or {}
        if cfg.get("notify_on_network_recovered") is False:
            return
        if not self._on_network_recovered:
            return
        try:
            self._on_network_recovered(payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "network recovery notify failed: %s",
                redact_exc(exc, max_len=128),
            )

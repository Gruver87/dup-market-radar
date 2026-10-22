"""ClientOid ledger — refuse duplicate live orders (local fail-closed)."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from radar.persist import atomic_write_json, read_json_object
from radar.secrets_redact import redact_text

logger = logging.getLogger("radar.trade.oid")


def _now_epoch_sec() -> int:
    return int(time.time())


def _entry_at_sec(entry: Dict[str, Any]) -> int:
    """Integer epoch seconds. Accepts legacy float `at` from older ledgers."""
    raw = entry.get("at")
    if raw is None:
        return 0
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return 0


class ClientOidLedger:
    """Persisted set of clientOids already submitted (or in-flight).

    claim() before POST — always sync-persisted (fail-closed before exchange).
    mark_accepted() / release() use write-behind flush (default 100ms) so the
    trade hot path does not fsync twice; crash before flush keeps the claim
    (duplicate refuse), which is the safe outcome.

    Corrupt / checksum-mismatch ledger → persist_ok=False and claim() raises
    (fail-closed: no live submit without durable duplicate protection).

    Timestamps are integer epoch seconds (no float money/time in the ledger).
    """

    def __init__(
        self,
        path: Path,
        *,
        ttl_sec: int = 604_800,
        max_entries: int = 10_000,
        write_behind_ms: int = 100,
    ) -> None:
        self._path = path
        self._ttl_sec = max(60, int(ttl_sec))
        self._max_entries = max(100, int(max_entries))
        self._write_behind_ms = max(0, int(write_behind_ms))
        self._lock = threading.Lock()
        self._entries: Dict[str, Dict[str, Any]] = {}
        self._persist_ok = True
        self._persist_error: Optional[str] = None
        self._integrity: str = "absent"
        self._dirty = False
        self._flush_timer: Optional[threading.Timer] = None
        self._closed = False
        self._load()

    def _load(self) -> None:
        data, err, meta = read_json_object(self._path)
        integrity = str(meta.get("integrity") or "absent")
        self._integrity = integrity
        if err or integrity in {"corrupt", "mismatch", "too_large"}:
            self._persist_ok = False
            self._persist_error = redact_text(
                err or f"oid ledger integrity={integrity}"
            )
            self._entries = {}
            logger.error("oid ledger unreadable — refuse claims: %s", self._persist_error)
            return
        raw = data.get("entries") if isinstance(data.get("entries"), dict) else {}
        self._entries = {str(k): v for k, v in raw.items() if isinstance(v, dict)}
        self._persist_ok = True
        self._persist_error = None
        self._prune_unlocked(persist=False)

    def _prune_unlocked(self, *, persist: bool) -> None:
        now = _now_epoch_sec()
        cutoff = now - self._ttl_sec
        dead = [k for k, v in self._entries.items() if _entry_at_sec(v) < cutoff]
        for k in dead:
            self._entries.pop(k, None)
        if len(self._entries) > self._max_entries:
            ordered = sorted(
                self._entries.items(),
                key=lambda kv: _entry_at_sec(kv[1]),
            )
            for k, _ in ordered[: len(self._entries) - self._max_entries]:
                self._entries.pop(k, None)
        if persist:
            self._save_unlocked()

    def _cancel_flush_timer_unlocked(self) -> None:
        t = self._flush_timer
        self._flush_timer = None
        if t is not None:
            t.cancel()

    def _schedule_flush_unlocked(self) -> None:
        """Debounce disk write for non-claim mutations."""
        self._dirty = True
        if self._write_behind_ms <= 0:
            self._save_unlocked()
            return
        if self._closed:
            self._save_unlocked()
            return
        if self._flush_timer is not None:
            return
        delay = self._write_behind_ms / 1000.0

        def _fire() -> None:
            with self._lock:
                self._flush_timer = None
                if not self._dirty:
                    return
                try:
                    self._save_unlocked()
                except Exception as exc:  # noqa: BLE001
                    self._persist_ok = False
                    self._persist_error = redact_text(str(exc))[:256]
                    logger.error(
                        "oid write-behind flush failed: %s", self._persist_error
                    )

        timer = threading.Timer(delay, _fire)
        timer.daemon = True
        self._flush_timer = timer
        timer.start()

    def _save_unlocked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            self._path,
            {"entries": self._entries, "updated_at": _now_epoch_sec()},
            with_checksum=True,
            backup_keep=2,
        )
        self._persist_ok = True
        self._persist_error = None
        self._integrity = "ok"
        self._dirty = False
        self._cancel_flush_timer_unlocked()

    def flush(self) -> None:
        """Sync pending write-behind mutations to disk."""
        with self._lock:
            if not self._dirty:
                self._cancel_flush_timer_unlocked()
                return
            try:
                self._save_unlocked()
            except Exception as exc:  # noqa: BLE001
                self._persist_ok = False
                self._persist_error = redact_text(str(exc))[:256]
                logger.error("oid flush failed: %s", self._persist_error)
                raise RuntimeError(
                    f"oid ledger flush failed: {self._persist_error}"
                ) from None

    def close(self) -> None:
        """Cancel timer and flush — call on API shutdown."""
        with self._lock:
            self._closed = True
            self._cancel_flush_timer_unlocked()
            if not self._dirty:
                return
            try:
                self._save_unlocked()
            except Exception as exc:  # noqa: BLE001
                self._persist_ok = False
                self._persist_error = redact_text(str(exc))[:256]
                logger.error("oid close flush failed: %s", self._persist_error)

    def seen(self, oid: str) -> bool:
        with self._lock:
            self._prune_unlocked(persist=False)
            return str(oid) in self._entries

    def claim(self, oid: str, *, side: str, symbol: str) -> bool:
        """Return True if newly claimed; False if duplicate.

        Always sync-persists before returning True (fail-closed before POST).
        Raises RuntimeError if ledger persist is degraded.
        """
        key = str(oid).strip()
        if not key:
            return False
        with self._lock:
            if not self._persist_ok:
                raise RuntimeError(
                    f"oid ledger persist degraded — refuse claim: "
                    f"{self._persist_error or self._integrity}"
                )
            self._prune_unlocked(persist=False)
            if key in self._entries:
                return False
            self._entries[key] = {
                "at": _now_epoch_sec(),
                "side": side,
                "symbol": symbol,
                "status": "claimed",
            }
            try:
                # Sync path — also drains any dirty mark_accepted/release.
                self._save_unlocked()
            except Exception as exc:  # noqa: BLE001
                self._entries.pop(key, None)
                self._persist_ok = False
                self._persist_error = redact_text(str(exc))[:256]
                logger.error("oid claim persist failed: %s", self._persist_error)
                raise RuntimeError(
                    f"oid ledger persist failed: {self._persist_error}"
                ) from None
            return True

    def mark_accepted(self, oid: str, *, order_id: Optional[str] = None) -> None:
        key = str(oid).strip()
        with self._lock:
            ent = self._entries.get(key)
            if not ent:
                return
            ent["status"] = "accepted"
            ent["at"] = _now_epoch_sec()
            if order_id:
                ent["order_id"] = str(order_id)
            try:
                self._schedule_flush_unlocked()
            except Exception as exc:  # noqa: BLE001
                self._persist_ok = False
                self._persist_error = redact_text(str(exc))[:256]
                logger.error(
                    "oid mark_accepted persist failed: %s", self._persist_error
                )

    def release(self, oid: str) -> None:
        """Drop claim — only for clear soft refuse / never-sent."""
        key = str(oid).strip()
        with self._lock:
            if key not in self._entries:
                return
            self._entries.pop(key, None)
            try:
                self._schedule_flush_unlocked()
            except Exception as exc:  # noqa: BLE001
                self._persist_ok = False
                self._persist_error = redact_text(str(exc))[:256]
                logger.error("oid release persist failed: %s", self._persist_error)

    def status(self) -> Dict[str, Any]:
        with self._lock:
            self._prune_unlocked(persist=False)
            return {
                "entries": len(self._entries),
                "ttl_sec": self._ttl_sec,
                "max_entries": self._max_entries,
                "write_behind_ms": self._write_behind_ms,
                "dirty": self._dirty,
                "path": str(self._path),
                "persist_ok": self._persist_ok,
                "persist_error": self._persist_error,
                "integrity": self._integrity,
            }

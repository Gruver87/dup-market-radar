"""Telegram-tracked positions (spot + futures watch) — integer money, checksum persist."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from radar.persist import atomic_write_json, read_json_object
from radar.secrets_redact import redact_text

logger = logging.getLogger("radar.telegram.watches")

# market: spot | futures_long | futures_short
VALID_MARKETS = frozenset({"spot", "futures_long", "futures_short"})
# Soft global cap — refuse new watches when exceeded (updates to existing keys OK).
DEFAULT_MAX_WATCHES = 2_000


class WatchBookFull(RuntimeError):
    """Soft refuse — watch book at capacity."""


def norm_symbol(symbol: str) -> str:
    s = (symbol or "").upper().replace("-", "").replace("/", "").replace("_", "")
    # Soft clamp for watch keys — trade path uses strict normalize_trade_symbol
    if len(s) > 32:
        s = s[:32]
    return s


class WatchBook:
    def __init__(
        self, path: Path, *, max_watches: int = DEFAULT_MAX_WATCHES
    ) -> None:
        self._path = path
        self._max_watches = max(1, int(max_watches))
        self._lock = threading.Lock()
        self._watches: Dict[str, Dict[str, Any]] = {}
        self._persist_ok = True
        self._persist_error: Optional[str] = None
        self._load()

    def _key(self, chat_id: int, symbol: str, market: str) -> str:
        return f"{int(chat_id)}:{norm_symbol(symbol)}:{market}"

    def _load(self) -> None:
        data, err, meta = read_json_object(self._path)
        if err or meta.get("integrity") in {"corrupt", "mismatch", "too_large"}:
            self._persist_ok = False
            self._persist_error = redact_text(
                err or f"integrity={meta.get('integrity')}"
            )[:256]
            self._watches = {}
            logger.error("watch book unreadable: %s", self._persist_error)
            return
        raw = data.get("watches") if isinstance(data.get("watches"), dict) else {}
        self._watches = {str(k): v for k, v in raw.items() if isinstance(v, dict)}
        self._persist_ok = True
        self._persist_error = None

    def _save_unlocked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            self._path,
            {"watches": self._watches, "updated_at": int(time.time())},
            with_checksum=True,
            backup_keep=3,
        )
        self._persist_ok = True
        self._persist_error = None

    @property
    def persist_ok(self) -> bool:
        return self._persist_ok

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "count": len(self._watches),
                "max_watches": self._max_watches,
                "persist_ok": self._persist_ok,
                "persist_error": self._persist_error,
                "path": str(self._path),
            }

    def add(
        self,
        chat_id: int,
        symbol: str,
        *,
        market: str = "spot",
        entry_price_i: Optional[int] = None,
        note: str = "",
    ) -> Dict[str, Any]:
        market = (market or "spot").lower().strip()
        if market not in VALID_MARKETS:
            raise ValueError(f"market must be one of {sorted(VALID_MARKETS)}")
        sym = norm_symbol(symbol)
        if not sym:
            raise ValueError("symbol required")
        key = self._key(chat_id, sym, market)
        row = {
            "chat_id": int(chat_id),
            "symbol": sym,
            "market": market,
            "entry_price_i": int(entry_price_i) if entry_price_i else None,
            "note": str(note or "")[:120],
            "opened_at_ms": int(time.time() * 1000),
            "last_alert_at_ms": 0,
            "last_alert_key": "",
        }
        with self._lock:
            if key not in self._watches and len(self._watches) >= self._max_watches:
                raise WatchBookFull(
                    f"watch book full ({self._max_watches}) — soft refuse"
                )
            self._watches[key] = row
            self._save_unlocked()
        return row

    def remove(self, chat_id: int, symbol: str, market: Optional[str] = None) -> int:
        sym = norm_symbol(symbol)
        removed = 0
        with self._lock:
            keys = list(self._watches.keys())
            for k in keys:
                w = self._watches[k]
                if int(w.get("chat_id") or 0) != int(chat_id):
                    continue
                if norm_symbol(str(w.get("symbol") or "")) != sym:
                    continue
                if market and w.get("market") != market:
                    continue
                self._watches.pop(k, None)
                removed += 1
            if removed:
                self._save_unlocked()
        return removed

    def list_for_chat(self, chat_id: int) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(v) for v in self._watches.values() if int(v.get("chat_id") or 0) == int(chat_id)]

    def all(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(v) for v in self._watches.values()]

    def touch_alert(self, chat_id: int, symbol: str, market: str, alert_key: str) -> None:
        key = self._key(chat_id, symbol, market)
        with self._lock:
            w = self._watches.get(key)
            if not w:
                return
            w["last_alert_at_ms"] = int(time.time() * 1000)
            w["last_alert_key"] = alert_key
            self._save_unlocked()

    def set_last_setup(
        self, chat_id: int, symbol: str, market: str, setup: str
    ) -> None:
        """Persist last observed scored setup (for outside-top fade alerts)."""
        setup_s = str(setup or "").strip()[:64]
        if not setup_s:
            return
        key = self._key(chat_id, symbol, market)
        with self._lock:
            w = self._watches.get(key)
            if not w:
                return
            if w.get("last_setup") == setup_s:
                return
            w["last_setup"] = setup_s
            w["last_setup_at_ms"] = int(time.time() * 1000)
            self._save_unlocked()

    def ensure(
        self,
        chat_id: int,
        symbol: str,
        *,
        market: str = "spot",
        entry_price_i: Optional[int] = None,
        note: str = "",
    ) -> bool:
        """Add only if missing. Returns True when newly created."""
        market = (market or "spot").lower().strip()
        if market not in VALID_MARKETS:
            raise ValueError(f"market must be one of {sorted(VALID_MARKETS)}")
        sym = norm_symbol(symbol)
        if not sym:
            raise ValueError("symbol required")
        key = self._key(chat_id, sym, market)
        with self._lock:
            if key in self._watches:
                return False
            if len(self._watches) >= self._max_watches:
                raise WatchBookFull(
                    f"watch book full ({self._max_watches}) — soft refuse"
                )
            self._watches[key] = {
                "chat_id": int(chat_id),
                "symbol": sym,
                "market": market,
                "entry_price_i": int(entry_price_i) if entry_price_i else None,
                "note": str(note or "")[:120],
                "opened_at_ms": int(time.time() * 1000),
                "last_alert_at_ms": 0,
                "last_alert_key": "",
            }
            self._save_unlocked()
            return True

    def prune_stale(self, ttl_sec: int) -> int:
        """Drop watches older than ttl_sec (by opened_at_ms). ttl_sec<=0 disables."""
        if ttl_sec <= 0:
            return 0
        cutoff = int(time.time() * 1000) - int(ttl_sec) * 1000
        removed = 0
        with self._lock:
            for k in list(self._watches.keys()):
                w = self._watches[k]
                opened = int(w.get("opened_at_ms") or 0)
                if opened and opened < cutoff:
                    self._watches.pop(k, None)
                    removed += 1
            if removed:
                self._save_unlocked()
                logger.info("pruned %s stale telegram watches ttl_sec=%s", removed, ttl_sec)
        return removed

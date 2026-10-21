"""Per-chat prefs (Telegram language) — atomic JSON + checksum."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from radar.config import ROOT
from radar.i18n import DEFAULT_LANG, Lang, normalize_lang
from radar.persist import atomic_write_json, read_json_object
from radar.secrets_redact import redact_text

logger = logging.getLogger("radar.prefs")

_DEFAULT_PATH = ROOT / "data" / "tg_prefs.json"
# Soft cap — prune least-recently-touched when exceeded (authorized chats only write).
MAX_PREF_CHATS = 500


class PrefsStore:
    def __init__(
        self, path: Optional[Path] = None, *, max_chats: int = MAX_PREF_CHATS
    ) -> None:
        self._path = path or _DEFAULT_PATH
        self._max_chats = max(1, int(max_chats))
        self._lock = threading.Lock()
        self._by_chat: Dict[str, Dict[str, Any]] = {}
        self._persist_ok = True
        self._persist_error: Optional[str] = None
        self._load()

    def _load(self) -> None:
        data, err, _meta = read_json_object(self._path, require_checksum=False)
        if err:
            self._persist_ok = False
            self._persist_error = redact_text(err)[:256]
            logger.error("prefs load failed: %s", self._persist_error)
            self._by_chat = {}
            return
        rows = data.get("chats") if isinstance(data, dict) else None
        out: Dict[str, Dict[str, Any]] = {}
        if isinstance(rows, dict):
            for k, v in rows.items():
                if isinstance(v, dict):
                    out[str(k)] = dict(v)
        self._by_chat = out
        self._persist_ok = True
        self._persist_error = None

    def _save_unlocked(self) -> None:
        try:
            atomic_write_json(self._path, {"chats": self._by_chat}, backup_keep=3)
            self._persist_ok = True
            self._persist_error = None
        except Exception as exc:  # noqa: BLE001
            self._persist_ok = False
            self._persist_error = redact_text(str(exc))[:256]
            logger.error("prefs save failed: %s", self._persist_error)

    def lang_for(self, chat_id: int) -> Lang:
        with self._lock:
            row = self._by_chat.get(str(int(chat_id))) or {}
            return normalize_lang(str(row.get("lang") or DEFAULT_LANG))

    def _prune_unlocked(self) -> None:
        """Drop oldest by touched_ms until under max_chats (fail-soft)."""
        while len(self._by_chat) > self._max_chats:
            oldest_key = None
            oldest_ts = None
            for k, v in self._by_chat.items():
                ts = int(v.get("touched_ms") or 0)
                if oldest_ts is None or ts < oldest_ts:
                    oldest_ts = ts
                    oldest_key = k
            if oldest_key is None:
                break
            self._by_chat.pop(oldest_key, None)

    def set_lang(self, chat_id: int, lang: str) -> Lang:
        normalized = normalize_lang(lang)
        with self._lock:
            key = str(int(chat_id))
            row = dict(self._by_chat.get(key) or {})
            row["lang"] = normalized
            row["touched_ms"] = int(time.time() * 1000)
            self._by_chat[key] = row
            self._prune_unlocked()
            self._save_unlocked()
            return normalized

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "chats": len(self._by_chat),
                "max_chats": self._max_chats,
                "persist_ok": self._persist_ok,
                "persist_error": self._persist_error,
                "path": str(self._path),
            }

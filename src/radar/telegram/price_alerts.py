"""Operator price-threshold alerts (absolute or ±% from mark). Integer money."""

from __future__ import annotations

import logging
import threading
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from radar.money import display_price, mul_div, price_i
from radar.persist import atomic_write_json, read_json_object
from radar.secrets_redact import redact_text
from radar.telegram.watches import norm_symbol

logger = logging.getLogger("radar.telegram.price_alerts")

DEFAULT_MAX = 500
COOLDOWN_MS = 300_000


class PriceAlertBookFull(RuntimeError):
    pass


def parse_price_target(
    raw: str, *, baseline_price_i: Optional[int]
) -> Tuple[str, int, str]:
    """Return (kind, target_price_i, direction) where direction is above|below.

    Absolute: ``95000`` / ``0.42`` → kind=abs.
    Relative: ``+5%`` / ``-3%`` → kind=pct from baseline mark.
    """
    s = (raw or "").strip().replace(",", "")
    if not s:
        raise ValueError("target required")
    if s.endswith("%"):
        if baseline_price_i is None or int(baseline_price_i) <= 0:
            raise ValueError("need current mark for % target")
        body = s[:-1].strip()
        sign = 1
        if body.startswith("+"):
            body = body[1:]
            sign = 1
        elif body.startswith("-"):
            body = body[1:]
            sign = -1
        try:
            pct = Decimal(body)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("bad % target") from exc
        # 5% → 500 bps (1% = 100 bps)
        pct_bps = int((pct * Decimal(100)).to_integral_value())
        if pct_bps <= 0 or pct_bps > 50_000:
            raise ValueError("% must be in (0, 500]")
        delta = mul_div(int(baseline_price_i), pct_bps * sign, 10_000)
        target = int(baseline_price_i) + int(delta)
        if target <= 0:
            raise ValueError("target price <= 0")
        direction = "above" if sign > 0 else "below"
        return "pct", target, direction
    target = int(price_i(s) or 0)
    if target <= 0:
        raise ValueError("bad absolute price")
    if baseline_price_i and int(baseline_price_i) > 0:
        direction = "above" if target >= int(baseline_price_i) else "below"
    else:
        direction = "above"
    return "abs", target, direction


class PriceAlertBook:
    def __init__(self, path: Path, *, max_alerts: int = DEFAULT_MAX) -> None:
        self._path = path
        self._max = max(1, int(max_alerts))
        self._lock = threading.Lock()
        self._rows: Dict[str, Dict[str, Any]] = {}
        self._persist_ok = True
        self._persist_error: Optional[str] = None
        self._load()

    def _key(self, chat_id: int, symbol: str, target_price_i: int) -> str:
        return f"{int(chat_id)}:{norm_symbol(symbol)}:{int(target_price_i)}"

    def _load(self) -> None:
        data, err, meta = read_json_object(self._path)
        if err or meta.get("integrity") in {"corrupt", "mismatch", "too_large"}:
            self._persist_ok = False
            self._persist_error = redact_text(
                err or f"integrity={meta.get('integrity')}"
            )[:256]
            self._rows = {}
            logger.error("price alerts unreadable: %s", self._persist_error)
            return
        raw = data.get("alerts") if isinstance(data.get("alerts"), dict) else {}
        self._rows = {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)}
        self._persist_ok = True
        self._persist_error = None

    def _save_unlocked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            self._path,
            {"alerts": self._rows, "updated_at": int(time.time())},
            with_checksum=True,
            backup_keep=3,
        )
        self._persist_ok = True
        self._persist_error = None

    def status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "count": len(self._rows),
                "max_alerts": self._max,
                "persist_ok": self._persist_ok,
                "persist_error": self._persist_error,
            }

    def add(
        self,
        chat_id: int,
        symbol: str,
        *,
        target_price_i: int,
        kind: str,
        direction: str,
        baseline_price_i: Optional[int] = None,
        note: str = "",
    ) -> Dict[str, Any]:
        sym = norm_symbol(symbol)
        if not sym:
            raise ValueError("symbol required")
        if int(target_price_i) <= 0:
            raise ValueError("target_price_i required")
        direction = direction if direction in {"above", "below"} else "above"
        kind = kind if kind in {"abs", "pct"} else "abs"
        key = self._key(chat_id, sym, int(target_price_i))
        row = {
            "chat_id": int(chat_id),
            "symbol": sym,
            "kind": kind,
            "direction": direction,
            "target_price_i": int(target_price_i),
            "baseline_price_i": int(baseline_price_i) if baseline_price_i else None,
            "note": str(note or "")[:64],
            "opened_at_ms": int(time.time() * 1000),
            "last_alert_at_ms": 0,
            "fired_count": 0,
        }
        with self._lock:
            if key not in self._rows and len(self._rows) >= self._max:
                raise PriceAlertBookFull(f"price alert book full ({self._max})")
            self._rows[key] = row
            self._save_unlocked()
        return row

    def remove(self, chat_id: int, symbol: str) -> int:
        sym = norm_symbol(symbol)
        removed = 0
        with self._lock:
            for k in list(self._rows.keys()):
                w = self._rows[k]
                if int(w.get("chat_id") or 0) != int(chat_id):
                    continue
                if norm_symbol(str(w.get("symbol") or "")) != sym:
                    continue
                self._rows.pop(k, None)
                removed += 1
            if removed:
                self._save_unlocked()
        return removed

    def list_for_chat(self, chat_id: int) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                dict(v)
                for v in self._rows.values()
                if int(v.get("chat_id") or 0) == int(chat_id)
            ]

    def all(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(v) for v in self._rows.values()]

    def mark_fired(self, chat_id: int, symbol: str, target_price_i: int) -> None:
        key = self._key(chat_id, symbol, target_price_i)
        with self._lock:
            row = self._rows.get(key)
            if not row:
                return
            row["last_alert_at_ms"] = int(time.time() * 1000)
            row["fired_count"] = int(row.get("fired_count") or 0) + 1
            self._save_unlocked()


def evaluate_price_alert(
    alert: Dict[str, Any],
    mark_price_i: Optional[int],
    *,
    cooldown_ms: int = COOLDOWN_MS,
) -> Optional[Dict[str, Any]]:
    """Return alert payload when mark crosses target; None otherwise."""
    if mark_price_i is None or int(mark_price_i) <= 0:
        return None
    target = int(alert.get("target_price_i") or 0)
    if target <= 0:
        return None
    direction = str(alert.get("direction") or "above")
    mark = int(mark_price_i)
    hit = mark >= target if direction == "above" else mark <= target
    if not hit:
        return None
    last = int(alert.get("last_alert_at_ms") or 0)
    now = int(time.time() * 1000)
    if last and now - last < int(cooldown_ms):
        return None
    return {
        "type": "price_target",
        "chat_id": int(alert.get("chat_id") or 0),
        "symbol": str(alert.get("symbol") or ""),
        "direction": direction,
        "kind": str(alert.get("kind") or "abs"),
        "target_price_i": target,
        "mark_price_i": mark,
        "message": (
            f"PRICE {'≥' if direction == 'above' else '≤'} "
            f"{display_price(target)} · mark {display_price(mark)} · "
            f"<code>{str(alert.get('symbol') or '')}</code>"
        ),
    }


def scan_price_alerts(
    book: PriceAlertBook,
    *,
    mark_fn,
    cooldown_ms: int = COOLDOWN_MS,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for alert in book.all():
        sym = str(alert.get("symbol") or "")
        try:
            mark = mark_fn(sym) if mark_fn else None
        except Exception:  # noqa: BLE001
            mark = None
        hit = evaluate_price_alert(alert, mark, cooldown_ms=cooldown_ms)
        if hit:
            out.append(hit)
            book.mark_fired(
                int(alert["chat_id"]),
                sym,
                int(alert["target_price_i"]),
            )
    return out

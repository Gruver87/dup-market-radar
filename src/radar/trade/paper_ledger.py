"""Paper / dry-run ledger — realized + mark-to-market PnL in USDT micros.

No exchange fills required. Integer money only. Fail-closed on corrupt file.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from radar.config import ROOT
from radar.money import PRICE_SCALE, QUOTE_SCALE, mul_div, notional_i_from_price_qty
from radar.persist import atomic_write_json, read_json_object
from radar.secrets_redact import redact_text

logger = logging.getLogger("radar.trade.paper")

DEFAULT_PATH = ROOT / "data" / "paper_ledger.json"


class PaperLedger:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or DEFAULT_PATH
        self._lock = threading.Lock()
        self._persist_ok = True
        self._persist_error: Optional[str] = None
        self._open: Dict[str, Dict[str, Any]] = {}
        self._closed: List[Dict[str, Any]] = []
        self._realized_micros = 0
        self._load()

    def _load(self) -> None:
        data, err, meta = read_json_object(self._path)
        if err or meta.get("integrity") in {"corrupt", "mismatch", "too_large"}:
            self._persist_ok = False
            self._persist_error = redact_text(
                err or f"integrity={meta.get('integrity')}"
            )[:256]
            self._open = {}
            self._closed = []
            self._realized_micros = 0
            logger.error("paper ledger unreadable: %s", self._persist_error)
            return
        if not data:
            self._persist_ok = True
            self._persist_error = None
            return
        raw_open = data.get("open") if isinstance(data.get("open"), dict) else {}
        self._open = {
            str(k).upper(): dict(v)
            for k, v in raw_open.items()
            if isinstance(v, dict) and k
        }
        closed = data.get("closed") if isinstance(data.get("closed"), list) else []
        self._closed = [dict(x) for x in closed if isinstance(x, dict)][-500:]
        self._realized_micros = int(data.get("realized_pnl_usdt_micros") or 0)
        self._persist_ok = True
        self._persist_error = None

    def _save_unlocked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            self._path,
            {
                "open": self._open,
                "closed": self._closed[-500:],
                "realized_pnl_usdt_micros": int(self._realized_micros),
                "updated_at_ms": int(time.time() * 1000),
                "quote_scale": QUOTE_SCALE,
                "price_scale": PRICE_SCALE,
            },
            with_checksum=True,
            backup_keep=3,
        )
        self._persist_ok = True
        self._persist_error = None

    def status(self, *, marks: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
        with self._lock:
            unrealized = 0
            open_rows = []
            for sym, pos in self._open.items():
                entry = int(pos.get("entry_price_i") or 0)
                size = int(pos.get("base_size_i") or 0)
                cost = int(pos.get("quote_usdt_micros") or 0)
                mark = None
                if marks and sym in marks:
                    mark = int(marks[sym])
                u = 0
                if mark and entry > 0 and size > 0:
                    # PnL micros ≈ size * (mark - entry) / 1e10
                    u = notional_i_from_price_qty(mark - entry, size)
                    unrealized += u
                open_rows.append(
                    {
                        "symbol": sym,
                        "entry_price_i": entry,
                        "base_size_i": size,
                        "quote_usdt_micros": cost,
                        "mark_price_i": mark,
                        "unrealized_pnl_usdt_micros": u,
                        "opened_at_ms": pos.get("opened_at_ms"),
                        "source": pos.get("source"),
                    }
                )
            return {
                "persist_ok": self._persist_ok,
                "persist_error": self._persist_error,
                "open_count": len(self._open),
                "closed_count": len(self._closed),
                "realized_pnl_usdt_micros": int(self._realized_micros),
                "unrealized_pnl_usdt_micros": int(unrealized),
                "total_pnl_usdt_micros": int(self._realized_micros) + int(unrealized),
                "open": open_rows,
                "closed_tail": list(self._closed[-20:]),
                "integer_money": True,
            }

    def record_event(self, event: Dict[str, Any]) -> None:
        """Apply dry_run (or explicit paper) buy/sell into the ledger."""
        if not event.get("ok"):
            return
        # Paper tracks dry_run by default; live fills stay on auto_positions.
        if not event.get("dry_run") and not event.get("paper"):
            return
        side = str(event.get("side") or "").lower()
        sym = str(event.get("symbol") or "").upper().replace("-", "")
        if not sym or side not in {"buy", "sell"}:
            return
        with self._lock:
            if not self._persist_ok:
                logger.warning("paper ledger refuse — persist degraded")
                return
            if side == "buy":
                entry = int(event.get("entry_price_i") or event.get("price_i") or 0)
                quote = int(event.get("quote_usdt_micros") or 0)
                size = int(event.get("base_size_i") or 0)
                if size <= 0 and entry > 0 and quote > 0:
                    size = mul_div(quote, PRICE_SCALE, entry)
                if entry <= 0 or size <= 0:
                    return
                self._open[sym] = {
                    "symbol": sym,
                    "entry_price_i": entry,
                    "base_size_i": size,
                    "quote_usdt_micros": quote or notional_i_from_price_qty(entry, size),
                    "opened_at_ms": int(time.time() * 1000),
                    "source": str(event.get("source") or "paper")[:32],
                }
            else:
                pos = self._open.pop(sym, None)
                if not pos:
                    return
                entry = int(pos.get("entry_price_i") or 0)
                size = int(pos.get("base_size_i") or 0)
                exit_px = int(
                    event.get("exit_price_i")
                    or event.get("price_i")
                    or event.get("mark_price_i")
                    or 0
                )
                if entry <= 0 or size <= 0:
                    return
                # Soft: missing mark → close at entry (0 PnL) rather than drop bag.
                if exit_px <= 0:
                    exit_px = entry
                pnl = notional_i_from_price_qty(exit_px - entry, size)
                self._realized_micros += pnl
                self._closed.append(
                    {
                        "symbol": sym,
                        "entry_price_i": entry,
                        "exit_price_i": exit_px,
                        "base_size_i": size,
                        "realized_pnl_usdt_micros": pnl,
                        "closed_at_ms": int(time.time() * 1000),
                        "reason": redact_text(str(event.get("reason") or ""))[:128],
                        "source": str(event.get("source") or "paper")[:32],
                    }
                )
            self._save_unlocked()

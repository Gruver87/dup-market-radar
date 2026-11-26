"""Daily live P/L mini-report from trade_events.jsonl (integer USDT micros)."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from radar.secrets_redact import redact_exc
from radar.trade.events import EVENTS_PATH

logger = logging.getLogger("radar.trade.daily_report")

_LIVE_SOURCES = frozenset({"auto", "manual", "telegram", "external_sync"})


def _proceeds(ev: Dict[str, Any]) -> int:
    for k in ("quote_usdt_micros", "deal_funds_micros"):
        try:
            n = int(ev.get(k) or 0)
            if n > 0:
                return n
        except (TypeError, ValueError):
            continue
    return 0


def _take_lot(queue: List[Dict[str, Any]], sell_qty: int) -> Optional[Dict[str, Any]]:
    if not queue:
        return None
    q = int(sell_qty or 0)
    if q > 0:
        for i, lot in enumerate(queue):
            if int(lot.get("base") or 0) == q:
                return queue.pop(i)
        best = None
        best_diff = 10**30
        for i, lot in enumerate(queue):
            b = int(lot.get("base") or 0)
            if b <= 0:
                continue
            d = abs(b - q)
            if d <= max(1, q * 2 // 100) and d < best_diff:
                best = i
                best_diff = d
        if best is not None:
            return queue.pop(best)
    return queue.pop(0)


def _day_utc(at_ms: int) -> str:
    return datetime.fromtimestamp(at_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")


def _read_all_events(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("daily report read failed: %s", redact_exc(exc, max_len=128))
        return []
    out: List[Dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    out.sort(key=lambda e: int(e.get("at_ms") or 0))
    return out


def build_daily_report(
    *,
    path: Path | None = None,
    days: int = 30,
) -> Dict[str, Any]:
    """Aggregate invested / realized P/L by UTC day since journal start.

    Invested = sum of live buy quote micros that day.
    Realized = closed-round PnL attributed to the **sell** day (size-aware FIFO).
    External/phone flats: invested still counted on buy day; PnL unknown (not invented).
    """
    n_days = max(1, min(90, int(days or 30)))
    events = _read_all_events(path or EVENTS_PATH)
    lots: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_day: Dict[str, Dict[str, Any]] = {}

    def bucket(day: str) -> Dict[str, Any]:
        if day not in by_day:
            by_day[day] = {
                "day_utc": day,
                "buy_usdt_micros": 0,
                "sell_usdt_micros": 0,
                "realized_pnl_usdt_micros": 0,
                "buys": 0,
                "sells": 0,
                "closed_rounds": 0,
                "wins": 0,
                "losses": 0,
                "external_exits": 0,
                "unknown_pnl_exits": 0,
            }
        return by_day[day]

    for ev in events:
        if not ev.get("ok") or ev.get("dry_run") or ev.get("skipped"):
            continue
        msg = str(ev.get("message") or "")
        if "preview" in msg.lower():
            continue
        src = str(ev.get("source") or "")
        if src not in _LIVE_SOURCES:
            continue
        at = int(ev.get("at_ms") or 0)
        if at <= 0:
            continue
        day = _day_utc(at)
        side = str(ev.get("side") or "").lower()
        sym = str(ev.get("symbol") or "").upper().replace("-", "").replace("/", "")
        if not sym:
            continue
        qty = int(ev.get("base_size_i") or 0)
        pr = _proceeds(ev)
        reason = str(ev.get("reason") or msg)
        b = bucket(day)

        if side == "buy" and pr > 0:
            if qty <= 0:
                continue
            b["buy_usdt_micros"] += pr
            b["buys"] += 1
            lots[sym].append({"cost": pr, "base": qty, "buy_day": day})
            continue

        if side != "sell":
            continue

        b["sells"] += 1
        if src == "external_sync" or "external_balance_flat" in reason or "sold outside" in reason.lower():
            if lots[sym]:
                _take_lot(lots[sym], qty)
            b["external_exits"] += 1
            continue

        lot = _take_lot(lots[sym], qty) if lots[sym] else None
        cost = int((lot or {}).get("cost") or ev.get("cost_usdt_micros") or 0)
        sell_q = pr
        pnl: Optional[int] = None
        if ev.get("realized_pnl_usdt_micros") is not None:
            try:
                pnl = int(ev["realized_pnl_usdt_micros"])
            except (TypeError, ValueError):
                pnl = None
            if cost <= 0 and sell_q > 0 and pnl is not None:
                cost = sell_q - pnl
        elif lot and sell_q > 0 and cost > 0:
            pnl = sell_q - cost
        # Refuse absurd reconstructed PnL without audited field.
        if (
            pnl is not None
            and ev.get("realized_pnl_usdt_micros") is None
            and cost > 0
            and abs(pnl) > cost * 0.5
        ):
            pnl = None

        if sell_q > 0:
            b["sell_usdt_micros"] += sell_q
        if pnl is None:
            b["unknown_pnl_exits"] += 1
            continue
        b["realized_pnl_usdt_micros"] += pnl
        b["closed_rounds"] += 1
        if pnl > 0:
            b["wins"] += 1
        elif pnl < 0:
            b["losses"] += 1

    days_sorted = sorted(by_day.keys(), reverse=True)[:n_days]
    rows = [by_day[d] for d in days_sorted]
    total_pnl = sum(int(r["realized_pnl_usdt_micros"]) for r in rows)
    total_buy = sum(int(r["buy_usdt_micros"]) for r in rows)
    return {
        "ok": True,
        "days": rows,
        "count": len(rows),
        "total_buy_usdt_micros": int(total_buy),
        "total_realized_pnl_usdt_micros": int(total_pnl),
        "timezone": "UTC",
        "note": "realized PnL on sell day; invested = buys that UTC day",
    }

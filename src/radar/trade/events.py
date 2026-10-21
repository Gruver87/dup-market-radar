"""Structured trade event audit — rotating JSONL (integer money fields only)."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List

from radar.audit_log import AuditLogError, append_jsonl
from radar.config import ROOT, load_config
from radar.request_ctx import get_request_id
from radar.secrets_redact import redact_exc, redact_text

logger = logging.getLogger("radar.trade.events")

EVENTS_PATH = ROOT / "data" / "trade_events.jsonl"


def record_trade_event(event: Dict[str, Any]) -> None:
    """Append one trade/ops event. Never raises to caller (log only)."""
    cfg = load_config().get("auto_trade") or {}
    max_bytes = int(cfg.get("audit_log_max_bytes") or 5_242_880)
    keep = int(cfg.get("audit_log_keep") if cfg.get("audit_log_keep") is not None else 5)
    rid = event.get("request_id") or get_request_id()
    row = {
        "at_ms": int(time.time() * 1000),
        "request_id": rid,
        "source": event.get("source") or "unknown",
        "side": event.get("side"),
        "symbol": event.get("symbol"),
        "ok": event.get("ok"),
        "dry_run": event.get("dry_run"),
        "message": (
            redact_text(str(event["message"]))[:512]
            if event.get("message") is not None
            else None
        ),
        "order_id": event.get("order_id"),
        "client_oid": event.get("client_oid"),
        "base_size_i": event.get("base_size_i"),
        "quote_usdt_micros": event.get("quote_usdt_micros"),
        "size_basis": event.get("size_basis"),
        "reason": (
            redact_text(str(event["reason"]))[:512]
            if event.get("reason") is not None
            else None
        ),
    }
    try:
        append_jsonl(EVENTS_PATH, row, max_bytes=max_bytes, keep=keep)
    except AuditLogError as exc:
        logger.error("trade event audit failed: %s", redact_exc(exc, max_len=256))
    except Exception as exc:  # noqa: BLE001
        logger.error("trade event unexpected: %s", redact_exc(exc, max_len=256))


# Bound memory when reading last-N from a rotated-but-still-large JSONL.
_EVENTS_TAIL_MAX_BYTES = 2_097_152  # 2 MiB


def _read_tail_text(path: Path, *, max_bytes: int) -> str:
    """Read at most the last max_bytes of a text file (UTF-8, soft)."""
    size = path.stat().st_size
    with path.open("rb") as fh:
        if size > max_bytes:
            fh.seek(size - max_bytes)
            raw = fh.read(max_bytes)
            # Drop partial first line after seek
            nl = raw.find(b"\n")
            if nl >= 0:
                raw = raw[nl + 1 :]
        else:
            raw = fh.read()
    return raw.decode("utf-8", errors="replace")


def read_trade_events(*, limit: int = 50) -> List[Dict[str, Any]]:
    """Read last N events (newest last). Soft-empty on missing/corrupt."""
    n = max(1, min(500, int(limit)))
    if not EVENTS_PATH.exists():
        return []
    try:
        text = _read_tail_text(EVENTS_PATH, max_bytes=_EVENTS_TAIL_MAX_BYTES)
        lines = text.splitlines()
    except OSError as exc:
        logger.warning("trade events unreadable: %s", redact_exc(exc, max_len=256))
        return []
    out: List[Dict[str, Any]] = []
    for line in lines[-n:]:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out

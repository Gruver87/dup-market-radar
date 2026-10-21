"""Auto buy/sell engine — off by default; never trades futures."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from radar.audit_log import AuditLogError, append_jsonl
from radar.config import ROOT, load_config
from radar.money import (
    PRICE_SCALE,
    QUOTE_SCALE,
    SCORE_CENTI,
    apply_fee_bps,
    drop_bps,
    mul_div,
    pct_to_bps,
    quote_i,
)
from radar.persist import atomic_write_json, read_json_object
from radar.secrets_redact import redact_exc, redact_text
from radar.trade.kucoin_manual import (
    KuCoinManualTrader,
    TradeRequest,
    extract_order_id,
    resolve_base_asset,
    sanitize_exchange_surface,
)

logger = logging.getLogger("radar.trade.auto")

STATE_PATH = ROOT / "data" / "auto_trade_state.json"
POS_PATH = ROOT / "data" / "auto_positions.json"
LOG_PATH = ROOT / "data" / "auto_trade_log.jsonl"

FUTURES_BLOCK = frozenset({"futures", "futures_long", "futures_short"})


class AutoTradeEngine:
    def __init__(
        self,
        trader: KuCoinManualTrader,
        snapshot_fn: Callable[[], Dict[str, Any]],
        on_action: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        self._trader = trader
        self._snapshot_fn = snapshot_fn
        self._on_action = on_action
        self._lock = threading.Lock()
        self._cycle_lock = threading.Lock()
        self._cycle_inflight = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_cycle: Dict[str, Any] = {}
        self._persist_ok = True
        self._persist_error: Optional[str] = None
        self._positions_sha256: Optional[str] = None
        self._positions_integrity: str = "absent"
        ROOT.joinpath("data").mkdir(parents=True, exist_ok=True)
        self._state = self._load_state()
        self._positions = self._load_positions_initial()

    def set_on_action(self, fn: Optional[Callable[[Dict[str, Any]], None]]) -> None:
        self._on_action = fn

    def _emit_action(self, action: Dict[str, Any]) -> None:
        if not self._on_action:
            return
        try:
            self._on_action(dict(action))
        except Exception as exc:  # noqa: BLE001
            logger.error("on_action callback failed: %s", redact_exc(exc, max_len=256))

    def status(self) -> Dict[str, Any]:
        cfg = (load_config().get("auto_trade") or {})
        with self._lock:
            positions = dict(self._positions)
            return {
                "enabled": bool(self._state.get("enabled")),
                "dry_run": bool(cfg.get("dry_run")),
                "futures_excluded": True,
                "allowed_sectors": list(cfg.get("allowed_sectors") or []),
                "quote_usdt_micros_per_buy": cfg.get("quote_usdt_micros_per_buy")
                or quote_i(cfg.get("quote_usdt_per_buy") or 10),
                "max_open_positions": cfg.get("max_open_positions"),
                "integer_money": True,
                "size_basis_default": "estimated_fee_adjusted",
                "fill_reconcile": bool(cfg.get("fill_reconcile", True)),
                "persistence_ok": self._persist_ok,
                "persistence_error": self._persist_error,
                "positions_sha256": self._positions_sha256,
                "positions_integrity": self._positions_integrity,
                "open_positions": len(positions),
                "positions": positions,
                "keys_configured": self._trader.ready,
                "last_cycle": self._last_cycle,
                "cycle_inflight": self._cycle_inflight,
                "manual_still_available": True,
            }

    def set_enabled(self, enabled: bool) -> Dict[str, Any]:
        enabled = bool(enabled)
        if enabled and not self._persist_ok:
            raise RuntimeError(
                "cannot enable auto-trade: positions file corrupt/unreadable "
                f"({self._persist_error}) — fix or remove data/auto_positions.json"
            )
        if enabled and not self._trader.ready:
            cfg = load_config().get("auto_trade") or {}
            if not cfg.get("dry_run"):
                raise RuntimeError(
                    "cannot enable auto-trade: KuCoin keys missing in .env "
                    "(or set auto_trade.dry_run: true for log-only mode)"
                )
        with self._lock:
            self._state["enabled"] = enabled
            self._state["updated_at"] = int(time.time())
            self._save_state()
        if enabled:
            self.start()
        logger.info("auto_trade enabled=%s", enabled)
        return self.status()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="radar-auto-trade", daemon=True)
        self._thread.start()

    def stop_loop(self, *, join_timeout_sec: float = 8.0) -> None:
        """Signal stop and join worker (fail-closed: do not leave orphan cycle)."""
        self._stop.set()
        thr = self._thread
        if thr is not None and thr.is_alive():
            thr.join(timeout=max(0.1, float(join_timeout_sec)))
            if thr.is_alive():
                logger.warning(
                    "auto-trade thread still alive after %.1fs join",
                    join_timeout_sec,
                )
            else:
                logger.info("auto-trade thread stopped")
        self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            cfg = load_config().get("auto_trade") or {}
            cycle = int(cfg.get("cycle_sec") or 90)
            try:
                if self._state.get("enabled"):
                    self.run_once()
            except Exception as exc:  # noqa: BLE001
                logger.error("auto cycle failed: %s", redact_exc(exc, max_len=256))
            self._stop.wait(cycle)

    def run_once(self) -> Dict[str, Any]:
        """Single-flight: concurrent callers get soft-refuse (no double buy/sell)."""
        if not self._cycle_lock.acquire(blocking=False):
            summary = {
                "at": int(time.time()),
                "ok": False,
                "enabled": bool(self._state.get("enabled")),
                "error": "cycle already in flight — soft refuse",
                "actions": [],
                "open_positions": len(self._positions),
                "cycle_inflight": True,
            }
            # Do not overwrite last successful cycle detail
            return summary
        self._cycle_inflight = True
        try:
            return self._run_once_unlocked()
        finally:
            self._cycle_inflight = False
            self._cycle_lock.release()

    def _run_once_unlocked(self) -> Dict[str, Any]:
        if not self._persist_ok:
            summary = {
                "at": int(time.time()),
                "ok": False,
                "enabled": bool(self._state.get("enabled")),
                "error": f"persistence degraded: {self._persist_error}",
                "actions": [],
                "open_positions": len(self._positions),
            }
            self._last_cycle = summary
            return summary

        cfg = load_config().get("auto_trade") or {}
        snap = self._snapshot_fn() or {}
        sectors = snap.get("sectors") or {}
        backend = str(snap.get("score_backend") or "")
        native_cfg = load_config().get("native") or {}
        backend_degraded = (
            backend.startswith("python_") or backend in {"", "none", "unavailable"}
        ) and bool(native_cfg.get("prefer"))
        analysis_degraded = (
            (not snap.get("ok", False))
            or (snap.get("mtf_ensure_ok") is False)
            or backend_degraded
        )
        allowed_preview = [
            s
            for s in (cfg.get("allowed_sectors") or [])
            if s not in FUTURES_BLOCK
        ]
        sectors_empty = not any(sectors.get(k) for k in allowed_preview)
        # Degraded / empty analysis: never buy. With open bags → stop-only via
        # exchange mark (audit must-fix). Without bags → refuse cleanly.
        if analysis_degraded or (sectors_empty and self._positions):
            reason = (
                "snapshot not ok"
                if not snap.get("ok", False)
                else (
                    "mtf ensure failed"
                    if snap.get("mtf_ensure_ok") is False
                    else (
                        f"score_backend degraded ({backend})"
                        if backend_degraded
                        else "sectors empty with open positions"
                    )
                )
            )
            if not self._positions:
                summary = {
                    "at": int(time.time()),
                    "ok": False,
                    "enabled": bool(self._state.get("enabled")),
                    "error": f"{reason} — auto cycle refused",
                    "actions": [],
                    "open_positions": 0,
                }
                self._last_cycle = summary
                logger.warning("auto cycle refused: %s", reason)
                return summary
            return self._run_stop_only_unlocked(cfg, reason=reason)
        allowed = [s for s in (cfg.get("allowed_sectors") or []) if s not in FUTURES_BLOCK]
        buy_setups = set(cfg.get("buy_setups") or ["buy-watch"])
        sell_setups = set(cfg.get("sell_setups") or ["fade-watch"])
        min_score_centi = int(
            cfg.get("min_score_centi")
            or ((int(cfg.get("min_score") or 55)) * SCORE_CENTI)
        )
        require_clean = bool(cfg.get("require_no_risk_flags", True))
        quote_micros = int(
            cfg.get("quote_usdt_micros_per_buy")
            or quote_i(cfg.get("quote_usdt_per_buy") or 10)
            or (10 * QUOTE_SCALE)
        )
        max_open = int(cfg.get("max_open_positions") or 5)
        max_buys = int(cfg.get("max_buys_per_cycle") or 2)
        max_sells = int(cfg.get("max_sells_per_cycle") or 5)
        stop_loss_bps = int(
            cfg.get("stop_loss_bps") or pct_to_bps(cfg.get("stop_loss_pct") or 12) or 1200
        )
        dry = bool(cfg.get("dry_run"))

        actions: List[Dict[str, Any]] = []

        # Refresh unreconciled bags before sell sizing (best-effort)
        if bool(cfg.get("fill_reconcile", True)) and not dry:
            refreshed = self.reconcile_positions()
            if refreshed.get("updated"):
                actions.append(
                    {
                        "ok": True,
                        "side": "reconcile",
                        "message": f"refreshed {len(refreshed['updated'])} positions",
                        "updated": refreshed["updated"],
                    }
                )

        with self._lock:
            positions = dict(self._positions)

        # --- sells first (free slots) ---
        sells = 0
        for sym, pos in list(positions.items()):
            if sells >= max_sells:
                break
            row = self._find_row(sectors, allowed, sym)
            reason = None
            if row:
                setup = row.get("setup")
                if setup in sell_setups:
                    reason = f"setup={setup}"
                price_i = row.get("price_i")
                entry_i = pos.get("entry_price_i")
                if price_i and entry_i and int(entry_i) > 0:
                    drop = drop_bps(int(entry_i), int(price_i))
                    if drop >= stop_loss_bps:
                        reason = f"stop_loss drop_bps={drop}"
            if not reason:
                continue
            size_i = int(pos.get("base_size_i") or 0)
            if size_i <= 0:
                continue
            result = self._exec_sell(sym, size_i, reason, dry)
            actions.append(result)
            self._emit_action({**result, "source": "auto", "entry_price_i": pos.get("entry_price_i")})
            sells += 1
            # Fail-closed: forget bag only after successful sell
            if result.get("ok"):
                with self._lock:
                    self._positions.pop(sym, None)
                    self._save_positions_locked()
                    positions = dict(self._positions)
            else:
                logger.warning(
                    "sell failed — keeping position %s: %s",
                    sym,
                    redact_text(str(result.get("message") or ""))[:256],
                )

        # --- buys ---
        with self._lock:
            positions = dict(self._positions)
        open_n = len(positions)
        buys = 0
        candidates = self._collect_buys(
            sectors, allowed, buy_setups, min_score_centi, require_clean, positions
        )
        for row in candidates:
            if buys >= max_buys or open_n >= max_open:
                break
            sym = row["symbol"]
            price_i = int(row.get("price_i") or 0)
            if price_i <= 0:
                continue
            last = self._exec_buy(sym, quote_micros, price_i, row.get("sector"), dry)
            actions.append(last)
            if last.get("ok"):
                est = apply_fee_bps(mul_div(quote_micros, PRICE_SCALE, price_i), 30)
                size_i = est
                size_basis = "estimated_fee_adjusted"
                order_id = last.get("order_id")
                fill_meta: Dict[str, Any] = {}
                if (
                    not dry
                    and not last.get("dry_run")
                    and bool(cfg.get("fill_reconcile", True))
                    and order_id
                ):
                    fill = self._reconcile_buy_fill(sym, str(order_id), cfg)
                    fill_meta = fill
                    if fill.get("ok") and int(fill.get("base_size_i") or 0) > 0:
                        size_i = int(fill["base_size_i"])
                        size_basis = str(fill.get("size_basis") or "kucoin_fill")
                    else:
                        logger.warning(
                            "fill reconcile failed for %s order=%s — keeping estimate: %s",
                            sym,
                            order_id,
                            fill.get("message"),
                        )
                        size_basis = "estimated_fee_adjusted_unreconciled"
                with self._lock:
                    pos: Dict[str, Any] = {
                        "symbol": sym,
                        "entry_price_i": price_i,
                        "quote_usdt_micros": quote_micros,
                        "base_size_i": size_i,
                        "size_basis": size_basis,
                        "sector": row.get("sector"),
                        "opened_at_ms": int(time.time() * 1000),
                        "dry_run": bool(dry or last.get("dry_run")),
                    }
                    if order_id:
                        pos["order_id"] = order_id
                    if fill_meta.get("deal_funds_micros"):
                        pos["deal_funds_micros"] = int(fill_meta["deal_funds_micros"])
                    if fill_meta.get("fee_base_i"):
                        pos["fee_base_i"] = int(fill_meta["fee_base_i"])
                    self._positions[sym] = pos
                    self._save_positions_locked()
                    open_n = len(self._positions)
                buys += 1
                last["base_size_i"] = size_i
                last["size_basis"] = size_basis
                last["entry_price_i"] = price_i
                self._emit_action({**last, "source": "auto"})

        with self._lock:
            open_n = len(self._positions)
        summary = {
            "at": int(time.time()),
            "ok": True,
            "enabled": True,
            "dry_run": dry,
            "persistence_ok": self._persist_ok,
            "actions": actions,
            "open_positions": open_n,
        }
        self._last_cycle = summary
        self._append_log(summary)
        return summary


    def _run_stop_only_unlocked(
        self, cfg: Dict[str, Any], *, reason: str
    ) -> Dict[str, Any]:
        """Sell-only on degraded snap: stop-loss via KuCoin mark; no buys / no fade."""
        dry = bool(cfg.get("dry_run"))
        stop_loss_bps = int(
            cfg.get("stop_loss_bps") or pct_to_bps(cfg.get("stop_loss_pct") or 12) or 1200
        )
        max_sells = int(cfg.get("max_sells_per_cycle") or 5)
        actions: List[Dict[str, Any]] = []
        with self._lock:
            positions = dict(self._positions)
        sells = 0
        mark_miss = 0
        for sym, pos in list(positions.items()):
            if sells >= max_sells:
                break
            entry_i = int(pos.get("entry_price_i") or 0)
            size_i = int(pos.get("base_size_i") or 0)
            if entry_i <= 0 or size_i <= 0:
                continue
            try:
                price_i = self._trader.get_spot_mark_price_i(str(sym))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "stop-only mark failed %s: %s",
                    sym,
                    redact_exc(exc, max_len=128),
                )
                price_i = None
            if not price_i:
                mark_miss += 1
                continue
            drop = drop_bps(entry_i, int(price_i))
            if drop < stop_loss_bps:
                continue
            sell_reason = f"stop_loss_degraded drop_bps={drop} ({reason})"
            result = self._exec_sell(sym, size_i, sell_reason, dry)
            actions.append(result)
            self._emit_action(
                {**result, "source": "auto", "entry_price_i": pos.get("entry_price_i")}
            )
            sells += 1
            if result.get("ok"):
                with self._lock:
                    self._positions.pop(sym, None)
                    self._save_positions_locked()
                    positions = dict(self._positions)
            else:
                logger.warning(
                    "stop-only sell failed %s: %s",
                    sym,
                    redact_text(str(result.get("message") or ""))[:256],
                )
        with self._lock:
            open_n = len(self._positions)
        err = f"{reason} — buys refused; stop-only"
        if mark_miss and not actions:
            err = f"{reason} — buys refused; mark unavailable for {mark_miss} bag(s)"
        summary = {
            "at": int(time.time()),
            "ok": False,
            "enabled": bool(self._state.get("enabled")),
            "dry_run": dry,
            "error": err,
            "mode": "stop_only",
            "actions": actions,
            "open_positions": open_n,
            "mark_miss": mark_miss,
        }
        self._last_cycle = summary
        self._append_log(summary)
        logger.warning(
            "auto stop-only cycle: reason=%s actions=%s mark_miss=%s",
            reason,
            len(actions),
            mark_miss,
        )
        return summary

    def _collect_buys(
        self,
        sectors: Dict[str, Any],
        allowed: List[str],
        buy_setups: set,
        min_score_centi: int,
        require_clean: bool,
        positions: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for sector in allowed:
            if sector in FUTURES_BLOCK:
                continue
            for row in sectors.get(sector) or []:
                if row.get("market") == "futures":
                    continue
                if row.get("venue") in {"dexscreener", "coingecko"}:
                    continue
                sym = row.get("symbol")
                if not sym or sym in positions:
                    continue
                if row.get("setup") not in buy_setups:
                    continue
                if int(row.get("score_centi") or 0) < int(min_score_centi):
                    continue
                if require_clean and (row.get("risk_flags") or []):
                    continue
                item = dict(row)
                item["sector"] = sector
                out.append(item)
        out.sort(key=lambda r: int(r.get("score_centi") or 0), reverse=True)
        return out

    def _find_row(
        self, sectors: Dict[str, Any], allowed: List[str], symbol: str
    ) -> Optional[Dict[str, Any]]:
        for sector in allowed:
            for row in sectors.get(sector) or []:
                if row.get("symbol") == symbol:
                    return row
        return None

    def _exec_buy(
        self, symbol: str, quote_micros: int, price_i: int, sector: Any, dry: bool
    ) -> Dict[str, Any]:
        if dry:
            msg = f"DRY buy {symbol} micros={quote_micros} @price_i={price_i}"
            logger.info(msg)
            return {"ok": True, "dry_run": True, "side": "buy", "symbol": symbol, "message": msg}
        client_oid = str(uuid.uuid4())
        result = self._trader.place(
            TradeRequest(
                symbol=symbol,
                side="buy",
                confirm=True,
                quote_usdt_micros=int(quote_micros),
                client_oid=client_oid,
            )
        )
        oid = extract_order_id(result.exchange)
        out: Dict[str, Any] = {
            "ok": result.ok,
            "dry_run": result.dry_run,
            "side": "buy",
            "symbol": symbol,
            "sector": sector,
            "message": result.message,
            "exchange": sanitize_exchange_surface(result.exchange),
            "client_oid": client_oid,
        }
        if oid:
            out["order_id"] = oid
        return out

    def _reconcile_buy_fill(
        self, symbol: str, order_id: str, cfg: Dict[str, Any]
    ) -> Dict[str, Any]:
        attempts = int(cfg.get("fill_reconcile_attempts") or 8)
        delay_ms = int(cfg.get("fill_reconcile_delay_ms") or 250)
        fill = self._trader.reconcile_fill(
            order_id,
            max_attempts=attempts,
            delay_ms=delay_ms,
            base_asset=resolve_base_asset(symbol),
        )
        return fill.as_dict()

    def reconcile_positions(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Re-poll fills for bags marked unreconciled / estimated. Persist on success."""
        if not self._persist_ok:
            return {
                "updated": [],
                "failed": [],
                "skipped": [],
                "error": f"persistence degraded: {self._persist_error}",
            }
        cfg = load_config().get("auto_trade") or {}
        with self._lock:
            targets = dict(self._positions)
        if symbol:
            sym = str(symbol).upper().replace("-", "").replace("/", "")
            # normalize keys: positions may be BTCUSDT
            matched = {
                k: v
                for k, v in targets.items()
                if k.upper().replace("-", "") == sym or k == symbol
            }
            targets = matched
        updated: List[Dict[str, Any]] = []
        failed: List[Dict[str, Any]] = []
        skipped: List[str] = []
        for sym, pos in targets.items():
            basis = str(pos.get("size_basis") or "")
            order_id = pos.get("order_id")
            needs = (
                "unreconciled" in basis
                or basis.startswith("estimated")
                or not basis
                or basis == "estimated_fee_adjusted"
            )
            # Always allow explicit single-symbol reconcile even if already fill
            if symbol is None and not needs:
                skipped.append(sym)
                continue
            if not order_id:
                skipped.append(sym)
                if symbol is not None:
                    failed.append({"symbol": sym, "message": "no order_id on position"})
                continue
            fill = self._reconcile_buy_fill(sym, str(order_id), cfg)
            if fill.get("ok") and int(fill.get("base_size_i") or 0) > 0:
                with self._lock:
                    cur = self._positions.get(sym)
                    if not cur:
                        continue
                    cur["base_size_i"] = int(fill["base_size_i"])
                    cur["size_basis"] = str(fill.get("size_basis") or "kucoin_fill")
                    if fill.get("deal_funds_micros"):
                        cur["deal_funds_micros"] = int(fill["deal_funds_micros"])
                    if fill.get("fee_base_i"):
                        cur["fee_base_i"] = int(fill["fee_base_i"])
                    cur["reconciled_at_ms"] = int(time.time() * 1000)
                    self._save_positions_locked()
                updated.append(
                    {
                        "symbol": sym,
                        "base_size_i": int(fill["base_size_i"]),
                        "size_basis": cur["size_basis"],
                    }
                )
            else:
                failed.append(
                    {
                        "symbol": sym,
                        "order_id": order_id,
                        "message": fill.get("message") or "reconcile failed",
                    }
                )
        return {"updated": updated, "failed": failed, "skipped": skipped}

    def _exec_sell(self, symbol: str, size_i: int, reason: str, dry: bool) -> Dict[str, Any]:
        if dry:
            msg = f"DRY sell {symbol} base_size_i={size_i} reason={reason}"
            logger.info(msg)
            return {"ok": True, "dry_run": True, "side": "sell", "symbol": symbol, "message": msg}
        client_oid = str(uuid.uuid4())
        result = self._trader.place(
            TradeRequest(
                symbol=symbol,
                side="sell",
                confirm=True,
                base_size_i=int(size_i),
                client_oid=client_oid,
            )
        )
        return {
            "ok": result.ok,
            "dry_run": result.dry_run,
            "side": "sell",
            "symbol": symbol,
            "message": result.message,
            "reason": reason,
            "exchange": sanitize_exchange_surface(result.exchange),
            "client_oid": client_oid,
            "order_id": extract_order_id(result.exchange),
        }

    def _load_state(self) -> Dict[str, Any]:
        cfg = load_config().get("auto_trade") or {}
        default = {"enabled": bool(cfg.get("enabled_default")), "updated_at": 0}
        data, err, _meta = read_json_object(STATE_PATH)
        if err:
            logger.warning(
                "auto state unreadable — using defaults: %s",
                redact_text(str(err))[:256],
            )
            return default
        return {**default, **data}

    def _save_state(self) -> None:
        atomic_write_json(STATE_PATH, self._state, with_checksum=True)

    def _load_positions_initial(self) -> Dict[str, Any]:
        data, err, meta = read_json_object(POS_PATH)
        self._positions_sha256 = meta.get("sha256")
        self._positions_integrity = str(meta.get("integrity") or "absent")
        if err or self._positions_integrity in {"corrupt", "mismatch", "too_large"}:
            self._persist_ok = False
            self._persist_error = redact_text(
                err or f"positions integrity={self._positions_integrity}"
            )[:256]
            return {}
        self._persist_ok = True
        self._persist_error = None
        return data

    def _save_positions_locked(self) -> None:
        """Caller must hold self._lock."""
        try:
            cfg = load_config().get("auto_trade") or {}
            keep = int(cfg.get("positions_backup_keep") if cfg.get("positions_backup_keep") is not None else 5)
            digest = atomic_write_json(
                POS_PATH,
                self._positions,
                with_checksum=True,
                backup_keep=keep,
            )
            self._positions_sha256 = digest
            self._positions_integrity = "ok"
            self._persist_ok = True
            self._persist_error = None
        except Exception as exc:  # noqa: BLE001
            safe = redact_exc(exc, max_len=256)
            self._persist_ok = False
            self._persist_error = safe
            self._positions_integrity = "corrupt"
            logger.error("failed to persist positions: %s", safe)

    def _append_log(self, summary: Dict[str, Any]) -> None:
        cfg = load_config().get("auto_trade") or {}
        max_bytes = int(cfg.get("audit_log_max_bytes") or 5_242_880)
        keep = int(cfg.get("audit_log_keep") if cfg.get("audit_log_keep") is not None else 5)
        try:
            append_jsonl(LOG_PATH, summary, max_bytes=max_bytes, keep=keep)
        except AuditLogError as exc:
            logger.error("audit log write failed: %s", redact_exc(exc, max_len=256))
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "audit log unexpected failure: %s", redact_exc(exc, max_len=256)
            )

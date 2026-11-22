"""Auto buy/sell engine — off by default; never trades futures.

Runtime toggle ``enabled`` is the opt-in **auto_execute** / autonomous mode.
When armed (enabled + keys + dry_run=false), qualifying buy setups (config
``buy_setups``, e.g. buy-watch / new-listing-watch / long) and fade-watch /
stop-loss sells place KuCoin spot market orders without a browser confirm
dialog. Manual Buy/Sell buttons keep their own preview/confirm.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from radar.audit_log import AuditLogError, append_jsonl
from radar.config import ROOT, load_config
from radar.money import (
    BASE_SCALE,
    PRICE_SCALE,
    QUOTE_SCALE,
    SCORE_CENTI,
    apply_fee_bps,
    drop_bps,
    mul_div,
    notional_i_from_price_qty,
    pct_to_bps,
    quote_i,
    rise_bps,
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


def _hard_risk_flags(flags: set[str], soft: set[str]) -> set[str]:
    """Return flags that are not soft-allowed (exact or ``prefix*`` patterns)."""
    hard: set[str] = set()
    for f in flags:
        if f in soft:
            continue
        matched = False
        for s in soft:
            if s.endswith("*") and len(s) > 1 and f.startswith(s[:-1]):
                matched = True
                break
        if not matched:
            hard.add(f)
    return hard


# Conservative lab defaults when config omits rails.
_DEFAULT_MAX_DAILY_BUYS = 20
_DEFAULT_MAX_DAILY_NOTIONAL_MICROS = 200 * QUOTE_SCALE  # 200 USDT
_DEFAULT_SIGNAL_COOLDOWN_SEC = 900  # 15m same symbol+side
_DEFAULT_PAPER_SEED_MICROS = 90 * QUOTE_SCALE  # dry-run seed when no KuCoin USDT
_SIZING_EQUAL = "equal_sleeves"
_SIZING_SHARED = "shared_pool"
_MIN_SHARED_BUY_MICROS = 1_000_000  # 1 USDT — refuse dust orders
_SIZING_FIXED = "fixed_quote"


def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _fire_key(symbol: str, side: str) -> str:
    sym = str(symbol).upper().replace("-", "").replace("/", "")
    return f"{sym}:{str(side).lower()}"


def split_equal_micros(amount_micro: int, parts: int) -> Tuple[int, ...]:
    """Split USDT micros into equal parts; remainder goes to the first part."""
    if (
        isinstance(amount_micro, bool)
        or isinstance(parts, bool)
        or not isinstance(amount_micro, int)
        or not isinstance(parts, int)
    ):
        return tuple()
    if amount_micro < 0 or parts < 1 or parts > 8 or amount_micro >= 10**24:
        return tuple()
    base = amount_micro // parts
    out = [base] * parts
    out[0] += amount_micro - base * parts
    return tuple(out)


def usdt_base_i_to_micros(base_size_i: int) -> int:
    """KuCoin USDT available is BASE_SCALE (1e8); auto quotes use QUOTE_SCALE (1e6)."""
    if isinstance(base_size_i, bool) or not isinstance(base_size_i, int) or base_size_i <= 0:
        return 0
    if base_size_i >= 10**24:
        return 0
    return int(base_size_i) * QUOTE_SCALE // BASE_SCALE


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
        dry = bool(cfg.get("dry_run"))
        with self._lock:
            positions = dict(self._positions)
            enabled = bool(self._state.get("enabled"))
            kill = bool(self._state.get("kill_switch"))
            daily = self._daily_snapshot_locked(cfg)
            keys_ok = self._trader.ready
            # Armed = live autonomous orders can fire (not dry_run log-only).
            armed = bool(
                enabled
                and not kill
                and self._persist_ok
                and not dry
                and keys_ok
            )
            sleeves = self._state.get("sleeves")
            if not isinstance(sleeves, dict):
                sleeves = {}
            sizing = str(cfg.get("sizing_mode") or _SIZING_FIXED).strip().lower()
            return {
                "enabled": enabled,
                # Alias: auto_execute == runtime enabled toggle (opt-in, OFF default).
                "auto_execute": enabled,
                "armed": armed,
                "kill_switch": kill,
                "kill_reason": self._state.get("kill_reason"),
                "kill_switch_at": self._state.get("kill_switch_at") or 0,
                "dry_run": dry,
                "futures_excluded": True,
                "allowed_sectors": list(cfg.get("allowed_sectors") or []),
                "symbol_blacklist": list(cfg.get("symbol_blacklist") or []),
                "symbol_whitelist": list(cfg.get("symbol_whitelist") or []),
                "sizing_mode": sizing,
                "sleeves": {str(k): int(v) for k, v in sleeves.items() if isinstance(v, int)},
                "sleeves_seed_usdt_micros": int(self._state.get("sleeves_seed_usdt_micros") or 0),
                "quote_usdt_micros_per_buy": cfg.get("quote_usdt_micros_per_buy")
                or quote_i(cfg.get("quote_usdt_per_buy") or 10),
                "max_open_positions": cfg.get("max_open_positions"),
                "max_daily_buys": int(
                    cfg.get("max_daily_buys")
                    if cfg.get("max_daily_buys") is not None
                    else _DEFAULT_MAX_DAILY_BUYS
                ),
                "max_daily_notional_usdt_micros": int(
                    cfg.get("max_daily_notional_usdt_micros")
                    if cfg.get("max_daily_notional_usdt_micros") is not None
                    else _DEFAULT_MAX_DAILY_NOTIONAL_MICROS
                ),
                "signal_cooldown_sec": int(
                    cfg.get("signal_cooldown_sec")
                    if cfg.get("signal_cooldown_sec") is not None
                    else _DEFAULT_SIGNAL_COOLDOWN_SEC
                ),
                "daily": daily,
                "integer_money": True,
                "size_basis_default": "estimated_fee_adjusted",
                "fill_reconcile": bool(cfg.get("fill_reconcile", True)),
                "persistence_ok": self._persist_ok,
                "persistence_error": self._persist_error,
                "positions_sha256": self._positions_sha256,
                "positions_integrity": self._positions_integrity,
                "open_positions": len(positions),
                "positions": positions,
                "keys_configured": keys_ok,
                "last_cycle": self._last_cycle,
                "cycle_inflight": self._cycle_inflight,
                "manual_still_available": True,
                "loop_alive": bool(self._thread and self._thread.is_alive()),
                "buy_setups": list(cfg.get("buy_setups") or ["buy-watch"]),
                "sell_setups": list(cfg.get("sell_setups") or ["fade-watch"]),
                "stop_loss_bps": int(
                    cfg.get("stop_loss_bps")
                    or pct_to_bps(cfg.get("stop_loss_pct") or 12)
                    or 1200
                ),
                "take_profit_bps": int(
                    cfg.get("take_profit_bps")
                    if cfg.get("take_profit_bps") is not None
                    else (
                        pct_to_bps(cfg.get("take_profit_pct") or 0) or 0
                    )
                ),
                "trailing_stop_bps": int(cfg.get("trailing_stop_bps") or 0),
                "trailing_offset_bps": int(cfg.get("trailing_offset_bps") or 0),
                "fade_min_abs_bps": int(
                    cfg.get("fade_min_abs_bps")
                    if cfg.get("fade_min_abs_bps") is not None
                    else 0
                ),
                "max_hold_sec": int(cfg.get("max_hold_sec") or 0),
                "roi_ladder": [
                    {"after_sec": a, "min_rise_bps": n}
                    for a, n in self._parse_roi_ladder(cfg)
                ],
                "fee_bps_per_side": int(cfg.get("fee_bps_per_side") or 0),
                "max_chase_change_bps_1h": int(
                    cfg.get("max_chase_change_bps_1h") or 0
                ),
                "loss_streak": int(self._state.get("loss_streak") or 0),
                "loss_streak_pause_buys": int(
                    cfg.get("loss_streak_pause_buys") or 0
                ),
                "pause_buys_until": int(self._state.get("pause_buys_until") or 0),
                "cycle_sec": int(cfg.get("cycle_sec") or 90),
            }

    def status_with_marks(self) -> Dict[str, Any]:
        """Status plus KuCoin mark / unrealized PnL on each open bag (USDT micros)."""
        st = self.status()
        positions = st.get("positions") or {}
        if not isinstance(positions, dict) or not positions:
            st["open_unrealized_pnl_usdt_micros"] = 0
            return st
        enriched: Dict[str, Any] = {}
        total_u = 0
        marks_ok = 0
        for sym, raw in positions.items():
            if not isinstance(raw, dict):
                continue
            row = dict(raw)
            cost = int(row.get("deal_funds_micros") or row.get("quote_usdt_micros") or 0)
            base = int(row.get("base_size_i") or 0)
            entry = int(row.get("entry_price_i") or 0)
            mark = 0
            try:
                mi = self._trader.get_spot_mark_price_i(str(sym))
                if mi and int(mi) > 0:
                    mark = int(mi)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "mark for open pnl failed %s: %s",
                    sym,
                    redact_exc(exc, max_len=96),
                )
            row["mark_price_i"] = mark if mark > 0 else None
            mark_val = 0
            if mark > 0 and base > 0:
                mark_val = int(notional_i_from_price_qty(mark, base))
            row["mark_value_usdt_micros"] = mark_val if mark_val > 0 else None
            upnl = None
            if mark_val > 0 and cost > 0:
                upnl = int(mark_val) - int(cost)
                total_u += upnl
                marks_ok += 1
            row["unrealized_pnl_usdt_micros"] = upnl
            if entry > 0 and mark > 0:
                # Signed bps vs entry: + = in profit for long spot bag.
                row["unrealized_pnl_bps"] = int(rise_bps(entry, mark))
            else:
                row["unrealized_pnl_bps"] = None
            enriched[str(sym)] = row
        st["positions"] = enriched
        st["open_unrealized_pnl_usdt_micros"] = int(total_u) if marks_ok else 0
        st["open_marks_ok"] = int(marks_ok)
        return st

    def loop_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    @staticmethod
    def _norm_symbol(symbol: str) -> str:
        return str(symbol or "").upper().replace("-", "").replace("/", "")

    def register_live_buy(
        self,
        *,
        symbol: str,
        quote_usdt_micros: int,
        base_size_i: int,
        entry_price_i: Optional[int] = None,
        order_id: Optional[str] = None,
        deal_funds_micros: Optional[int] = None,
        size_basis: str = "kucoin_fill",
        sector: str = "spot_alts",
        dry_run: bool = False,
        source: str = "manual",
        replace: bool = False,
    ) -> Dict[str, Any]:
        """Track a live spot buy in the open-position book (manual/TG/orphan recovery)."""
        if not self._persist_ok:
            return {
                "ok": False,
                "message": f"persistence degraded: {self._persist_error}",
            }
        sym = self._norm_symbol(symbol)
        if not sym.endswith("USDT"):
            return {"ok": False, "message": "only USDT spot pairs tracked"}
        q = int(quote_usdt_micros or 0)
        b = int(base_size_i or 0)
        if q <= 0 or b <= 0:
            return {"ok": False, "message": "need quote_usdt_micros and base_size_i > 0"}
        entry = int(entry_price_i or 0)
        if entry <= 0:
            entry = int(mul_div(q, 10_000_000_000, b))
        funds = int(deal_funds_micros or q)
        with self._lock:
            if sym in self._positions and not replace:
                return {
                    "ok": True,
                    "skipped": True,
                    "symbol": sym,
                    "message": "already tracked",
                }
            pos: Dict[str, Any] = {
                "symbol": sym,
                "entry_price_i": entry,
                "quote_usdt_micros": q,
                "deal_funds_micros": funds,
                "base_size_i": b,
                "size_basis": str(size_basis or "kucoin_fill")[:64],
                "sector": str(sector or "spot_alts")[:32],
                "opened_at_ms": int(time.time() * 1000),
                "dry_run": bool(dry_run),
                "source": str(source or "manual")[:32],
            }
            if order_id:
                pos["order_id"] = str(order_id)[:64]
            self._positions[sym] = pos
            self._save_positions_locked()
        logger.info(
            "registered live buy %s qty_i=%s cost_micros=%s source=%s",
            sym,
            b,
            funds,
            source,
        )
        return {"ok": True, "symbol": sym, "position": dict(pos)}

    def forget_position(self, symbol: str) -> Dict[str, Any]:
        """Drop a tracked bag after a live sell (manual/TG)."""
        sym = self._norm_symbol(symbol)
        with self._lock:
            if sym not in self._positions:
                return {"ok": True, "skipped": True, "symbol": sym}
            self._positions.pop(sym, None)
            self._save_positions_locked()
        return {"ok": True, "symbol": sym, "removed": True}

    def drop_externally_closed(self) -> Dict[str, Any]:
        """Forget bags whose KuCoin trade balance is flat (sold outside Radar).

        Honest: no invented sell USDT/PnL — emit a sync event so the UI drops the
        open row. Operator sold on phone/web; Radar only tracked the buy.
        """
        if not self._persist_ok or not self._trader.ready:
            return {
                "ok": False,
                "dropped": [],
                "message": "persist or keys not ready",
            }
        with self._lock:
            tracked = {
                sym: dict(pos)
                for sym, pos in self._positions.items()
                if isinstance(pos, dict)
            }
        dropped: List[str] = []
        kept: List[str] = []
        errors: List[Dict[str, str]] = []
        for sym, pos in tracked.items():
            if pos.get("dry_run"):
                kept.append(sym)
                continue
            base_ccy = resolve_base_asset(sym)
            try:
                bal_i = int(self._trader.get_available_base_i(base_ccy))
            except Exception as exc:  # noqa: BLE001
                errors.append({"symbol": sym, "reason": redact_exc(exc, max_len=96)})
                continue
            need = int(pos.get("base_size_i") or 0)
            # Flat or dust relative to tracked size (<1%).
            flat = bal_i <= 0 or (need > 0 and bal_i * 100 < need)
            if not flat:
                kept.append(sym)
                continue
            self.forget_position(sym)
            dropped.append(sym)
            self._emit_action(
                {
                    "ok": True,
                    "side": "sell",
                    "symbol": sym,
                    "source": "external_sync",
                    "dry_run": False,
                    "message": "KuCoin balance flat — sold outside Radar (PnL unknown)",
                    "reason": "external_balance_flat",
                    "base_size_i": need if need > 0 else None,
                    "cost_usdt_micros": int(
                        pos.get("deal_funds_micros")
                        or pos.get("quote_usdt_micros")
                        or 0
                    )
                    or None,
                    "entry_price_i": pos.get("entry_price_i"),
                }
            )
        return {
            "ok": True,
            "dropped": dropped,
            "kept": kept,
            "errors": errors,
        }

    def recover_untracked_buys(self, *, limit_events: int = 200) -> Dict[str, Any]:
        """Adopt live buys still on KuCoin that are missing from the position book.

        Manual buys historically only hit the event log — orphan recovery closes that gap
        when trade-account balance still holds the base asset.
        """
        from radar.trade.events import read_trade_events

        if not self._persist_ok:
            return {"ok": False, "adopted": [], "message": "persistence degraded"}
        if not self._trader.ready:
            return {"ok": False, "adopted": [], "message": "keys not ready"}
        events = read_trade_events(limit=max(1, min(500, int(limit_events))))
        open_lots: Dict[str, Dict[str, Any]] = {}
        for ev in events:
            if ev.get("ok") is not True or ev.get("dry_run") or ev.get("skipped"):
                continue
            side = str(ev.get("side") or "").lower()
            sym = self._norm_symbol(str(ev.get("symbol") or ""))
            if not sym:
                continue
            if side == "buy":
                q = int(ev.get("quote_usdt_micros") or ev.get("deal_funds_micros") or 0)
                b = int(ev.get("base_size_i") or 0)
                if q <= 0 or b <= 0:
                    continue
                open_lots[sym] = {
                    "quote_usdt_micros": q,
                    "base_size_i": b,
                    "entry_price_i": int(ev.get("entry_price_i") or 0),
                    "order_id": ev.get("order_id"),
                    "deal_funds_micros": int(
                        ev.get("deal_funds_micros") or q
                    ),
                    "size_basis": str(ev.get("size_basis") or "kucoin_fill"),
                    "source": str(ev.get("source") or "manual"),
                }
            elif side == "sell":
                open_lots.pop(sym, None)

        adopted: List[str] = []
        skipped: List[Dict[str, str]] = []
        for sym, lot in open_lots.items():
            with self._lock:
                if sym in self._positions:
                    skipped.append({"symbol": sym, "reason": "already_tracked"})
                    continue
            base_ccy = resolve_base_asset(sym)
            try:
                bal_i = int(self._trader.get_available_base_i(base_ccy))
            except Exception as exc:  # noqa: BLE001
                skipped.append(
                    {
                        "symbol": sym,
                        "reason": redact_exc(exc, max_len=96),
                    }
                )
                continue
            need = int(lot["base_size_i"])
            # Still holding at least half the recorded fill (fees / partials).
            if bal_i <= 0 or bal_i * 2 < need:
                skipped.append(
                    {
                        "symbol": sym,
                        "reason": f"balance_i={bal_i} below tracked={need}",
                    }
                )
                continue
            # Prefer live balance as size (honest remaining bag).
            use_size = bal_i if bal_i <= need * 2 else need
            res = self.register_live_buy(
                symbol=sym,
                quote_usdt_micros=int(lot["quote_usdt_micros"]),
                base_size_i=int(use_size),
                entry_price_i=int(lot["entry_price_i"] or 0) or None,
                order_id=str(lot["order_id"]) if lot.get("order_id") else None,
                deal_funds_micros=int(lot["deal_funds_micros"]),
                size_basis=str(lot["size_basis"]),
                sector="spot_alts",
                dry_run=False,
                source=f"recover:{lot.get('source') or 'manual'}",
            )
            if res.get("ok") and not res.get("skipped"):
                adopted.append(sym)
            else:
                skipped.append(
                    {
                        "symbol": sym,
                        "reason": str(res.get("message") or "register failed"),
                    }
                )
        return {"ok": True, "adopted": adopted, "skipped": skipped}

    def set_enabled(self, enabled: bool) -> Dict[str, Any]:
        enabled = bool(enabled)
        if enabled and not self._persist_ok:
            raise RuntimeError(
                "cannot enable auto-trade: positions file corrupt/unreadable "
                f"({self._persist_error}) — fix or remove data/auto_positions.json"
            )
        with self._lock:
            if enabled and self._state.get("kill_switch"):
                raise RuntimeError(
                    "cannot enable auto-execute: kill-switch is latched — "
                    "POST /api/trade/auto/clear-kill first"
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
            if enabled:
                cfg_on = load_config().get("auto_trade") or {}
                seed_err = self._ensure_sleeves_locked(cfg_on, force=False)
                if seed_err:
                    logger.warning("sleeve seed on enable: %s", seed_err)
            self._save_state()
        if enabled:
            self.start()
        logger.info("auto_execute enabled=%s", enabled)
        return self.status()

    def trip_kill_switch(self, reason: str = "ui_emergency_stop") -> Dict[str, Any]:
        """Immediately disable auto_execute and latch until clear-kill."""
        reason_s = redact_text(str(reason or "ui_emergency_stop"))[:128]
        with self._lock:
            self._state["enabled"] = False
            self._state["kill_switch"] = True
            self._state["kill_reason"] = reason_s
            self._state["kill_switch_at"] = int(time.time())
            self._state["updated_at"] = int(time.time())
            self._save_state()
        logger.warning("auto_execute kill-switch latched: %s", reason_s)
        return self.status()

    def clear_kill_switch(self) -> Dict[str, Any]:
        """Clear kill latch only — forces enabled OFF; does not arm auto_execute."""
        with self._lock:
            self._state["kill_switch"] = False
            self._state["kill_reason"] = None
            self._state["kill_switch_at"] = 0
            self._state["enabled"] = False
            self._state["updated_at"] = int(time.time())
            self._save_state()
        logger.info("auto_execute kill-switch cleared (still OFF until enable)")
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
                with self._lock:
                    armed_toggle = bool(
                        self._state.get("enabled") and not self._state.get("kill_switch")
                    )
                if armed_toggle:
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

        with self._lock:
            if self._state.get("kill_switch"):
                summary = {
                    "at": int(time.time()),
                    "ok": False,
                    "enabled": False,
                    "auto_execute": False,
                    "kill_switch": True,
                    "error": "kill-switch latched — auto cycle refused",
                    "actions": [],
                    "open_positions": len(self._positions),
                }
                self._last_cycle = summary
                return summary
            if not self._state.get("enabled"):
                summary = {
                    "at": int(time.time()),
                    "ok": False,
                    "enabled": False,
                    "auto_execute": False,
                    "error": "auto_execute OFF — cycle refused",
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
        listings_soft = {
            str(x)
            for x in (cfg.get("listings_soft_risk_flags") or ["cross_venue_live"])
            if x
        }
        memes_soft = {
            str(x)
            for x in (cfg.get("memes_soft_risk_flags") or ["below_vol_floor"])
            if x
        }
        spot_alts_soft = {
            str(x)
            for x in (cfg.get("spot_alts_soft_risk_flags") or [])
            if x
        }
        exec_venues = {
            str(v).strip().lower()
            for v in (cfg.get("exec_venues") or ["kucoin"])
            if v and str(v).strip()
        }
        fixed_quote_micros = int(
            cfg.get("quote_usdt_micros_per_buy")
            or quote_i(cfg.get("quote_usdt_per_buy") or 10)
            or (10 * QUOTE_SCALE)
        )
        equal_sleeves = self._sizing_equal(cfg)
        shared_pool = self._sizing_shared(cfg)
        max_open = int(cfg.get("max_open_positions") or 5)
        # 0 = unlimited per cycle (operator may open daily caps for measurement).
        max_buys = int(
            cfg.get("max_buys_per_cycle")
            if cfg.get("max_buys_per_cycle") is not None
            else 2
        )
        max_sells = int(
            cfg.get("max_sells_per_cycle")
            if cfg.get("max_sells_per_cycle") is not None
            else 5
        )
        max_daily_buys = int(
            cfg.get("max_daily_buys")
            if cfg.get("max_daily_buys") is not None
            else _DEFAULT_MAX_DAILY_BUYS
        )
        max_daily_notional = int(
            cfg.get("max_daily_notional_usdt_micros")
            if cfg.get("max_daily_notional_usdt_micros") is not None
            else _DEFAULT_MAX_DAILY_NOTIONAL_MICROS
        )
        cooldown_sec = int(
            cfg.get("signal_cooldown_sec")
            if cfg.get("signal_cooldown_sec") is not None
            else _DEFAULT_SIGNAL_COOLDOWN_SEC
        )
        stop_loss_bps = int(
            cfg.get("stop_loss_bps") or pct_to_bps(cfg.get("stop_loss_pct") or 12) or 1200
        )
        take_profit_bps = int(
            cfg.get("take_profit_bps")
            if cfg.get("take_profit_bps") is not None
            else (pct_to_bps(cfg.get("take_profit_pct") or 0) or 0)
        )
        trailing_stop_bps = int(cfg.get("trailing_stop_bps") or 0)
        trailing_offset_bps = int(cfg.get("trailing_offset_bps") or 0)
        fade_min_abs_bps = int(
            cfg.get("fade_min_abs_bps")
            if cfg.get("fade_min_abs_bps") is not None
            else 0
        )
        max_hold_sec = int(cfg.get("max_hold_sec") or 0)
        roi_ladder = self._parse_roi_ladder(cfg)
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

        # Drop bags sold on phone/web (balance flat) — not only Radar-path sells.
        if not dry and self._trader.ready:
            sync = self.drop_externally_closed()
            if sync.get("dropped"):
                actions.append(
                    {
                        "ok": True,
                        "side": "external_sync",
                        "message": f"dropped flat bags {sync.get('dropped')}",
                        "dropped": sync.get("dropped"),
                    }
                )

        with self._lock:
            positions = dict(self._positions)

        # --- sells first (free slots) ---
        # Every open bag: mark each cycle. Priority: SL → max-hold → trail → TP → ROI → fade.
        sells = 0
        for sym, pos in list(positions.items()):
            if max_sells > 0 and sells >= max_sells:
                break
            row = self._find_row(sectors, allowed, sym)
            reason, mark_i, pos_updates = self._evaluate_sell_reason(
                sym,
                pos,
                row=row,
                sell_setups=sell_setups,
                stop_loss_bps=stop_loss_bps,
                take_profit_bps=take_profit_bps,
                prefer_mark=bool(not dry and self._trader.ready),
                trailing_stop_bps=trailing_stop_bps,
                trailing_offset_bps=trailing_offset_bps,
                fade_min_abs_bps=fade_min_abs_bps,
                max_hold_sec=max_hold_sec,
                roi_ladder=roi_ladder,
            )
            if pos_updates:
                self._apply_pos_updates(sym, pos_updates)
                # Keep local copy coherent for this cycle.
                pos = dict(pos)
                pos.update(pos_updates)
                positions[sym] = pos
            if not reason:
                continue
            # Fade/setup cooldown; hard exits (SL/TP/trail/ROI/max-hold) always may fire.
            is_hard = any(
                str(reason).startswith(p)
                for p in (
                    "stop_loss",
                    "take_profit",
                    "trailing_stop",
                    "roi_ladder",
                    "max_hold",
                )
            )
            if not is_hard:
                blocked, block_msg = self._cooldown_blocked(sym, "sell", cooldown_sec)
                if blocked:
                    actions.append(
                        {
                            "ok": False,
                            "side": "sell",
                            "symbol": sym,
                            "skipped": True,
                            "message": block_msg,
                        }
                    )
                    continue
            size_i = int(pos.get("base_size_i") or 0)
            if size_i <= 0:
                continue
            result = self._exec_sell(
                sym, size_i, reason, dry, mark_price_i=mark_i if mark_i > 0 else None
            )
            actions.append(result)
            cost_m = int(
                pos.get("deal_funds_micros") or pos.get("quote_usdt_micros") or 0
            )
            proceeds_m = int(
                result.get("quote_usdt_micros")
                or result.get("deal_funds_micros")
                or 0
            )
            sell_ev: Dict[str, Any] = {
                **result,
                "source": "auto",
                "entry_price_i": pos.get("entry_price_i"),
                "base_size_i": int(result.get("base_size_i") or size_i),
                "cost_usdt_micros": cost_m if cost_m > 0 else None,
            }
            if proceeds_m > 0:
                sell_ev["quote_usdt_micros"] = proceeds_m
            if (
                cost_m > 0
                and proceeds_m > 0
                and result.get("ok")
                and not result.get("skipped")
            ):
                gross, fee_m, net = self._pnl_after_fees(cost_m, proceeds_m, cfg)
                sell_ev["realized_pnl_gross_usdt_micros"] = gross
                sell_ev["fee_usdt_micros"] = fee_m
                sell_ev["realized_pnl_usdt_micros"] = net
            if not sell_ev.get("exit_price_i") and mark_i > 0:
                sell_ev["exit_price_i"] = mark_i
            self._emit_action(sell_ev)
            sells += 1
            # Fail-closed: forget bag only after successful sell
            if result.get("ok"):
                credit = self._sell_credit_micros(pos, result, row=row, dry=dry)
                sector = str(pos.get("sector") or (row or {}).get("sector") or "")
                with self._lock:
                    self._positions.pop(sym, None)
                    self._save_positions_locked()
                    positions = dict(self._positions)
                    if equal_sleeves and sector and credit > 0:
                        self._credit_sleeve_locked(sector, credit)
                    pnl_net = sell_ev.get("realized_pnl_usdt_micros")
                    if isinstance(pnl_net, int):
                        self._note_sell_pnl_locked(pnl_net, cfg)
                    self._record_fire_locked(sym, "sell", notional_micros=0, is_buy=False)
            else:
                logger.warning(
                    "sell failed — keeping position %s: %s",
                    sym,
                    redact_text(str(result.get("message") or ""))[:256],
                )

        # --- buys ---
        with self._lock:
            positions = dict(self._positions)
            if equal_sleeves:
                seed_err = self._ensure_sleeves_locked(cfg, force=False)
                if seed_err:
                    actions.append(
                        {
                            "ok": False,
                            "side": "buy",
                            "skipped": True,
                            "message": f"sleeve seed failed: {seed_err}",
                        }
                    )
        open_n = len(positions)
        buys = 0
        candidates = self._collect_buys(
            sectors,
            allowed,
            buy_setups,
            min_score_centi,
            require_clean,
            positions,
            soft_flags_by_sector={
                "listings": listings_soft,
                "memes": memes_soft,
                "spot_alts": spot_alts_soft,
            },
            exec_venues=exec_venues,
        )
        for row in candidates:
            if (max_buys > 0 and buys >= max_buys) or open_n >= max_open:
                break
            sym = row["symbol"]
            price_i = int(row.get("price_i") or 0)
            sector = str(row.get("sector") or "")
            if price_i <= 0:
                continue
            chase_cap = int(cfg.get("max_chase_change_bps_1h") or 0)
            if chase_cap > 0:
                metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
                chg_1h = row.get("change_bps_1h")
                if chg_1h is None:
                    chg_1h = metrics.get("change_bps_1h")
                try:
                    chg_i = int(chg_1h) if chg_1h is not None else None
                except (TypeError, ValueError):
                    chg_i = None
                if chg_i is not None and chg_i >= chase_cap:
                    actions.append(
                        {
                            "ok": False,
                            "side": "buy",
                            "symbol": sym,
                            "skipped": True,
                            "message": (
                                f"anti_chase change_bps_1h={chg_i} >= {chase_cap}"
                            ),
                        }
                    )
                    continue
            if equal_sleeves:
                if self._sector_has_open(sector, positions):
                    actions.append(
                        {
                            "ok": False,
                            "side": "buy",
                            "symbol": sym,
                            "skipped": True,
                            "message": f"sleeve {sector} already has open position",
                        }
                    )
                    continue
                quote_micros, sleeve_err = self._sleeve_buy_quote(sector, cfg)
                if sleeve_err or quote_micros <= 0:
                    actions.append(
                        {
                            "ok": False,
                            "side": "buy",
                            "symbol": sym,
                            "skipped": True,
                            "message": sleeve_err or f"sleeve {sector} empty",
                        }
                    )
                    continue
            elif shared_pool:
                # One cash pool (KuCoin free USDT) — any allowed sector, no 1-per-sleeve lock.
                quote_micros, pool_err = self._shared_pool_buy_quote(
                    cfg, open_n=open_n, max_open=max_open
                )
                if pool_err or quote_micros <= 0:
                    actions.append(
                        {
                            "ok": False,
                            "side": "buy",
                            "symbol": sym,
                            "skipped": True,
                            "message": pool_err or "shared pool empty",
                        }
                    )
                    continue
            else:
                quote_micros = fixed_quote_micros
            gate_ok, gate_msg = self._buy_gates_ok(
                sym,
                quote_micros,
                max_daily_buys=max_daily_buys,
                max_daily_notional=max_daily_notional,
                cooldown_sec=cooldown_sec,
            )
            if not gate_ok:
                actions.append(
                    {
                        "ok": False,
                        "side": "buy",
                        "symbol": sym,
                        "skipped": True,
                        "message": gate_msg,
                    }
                )
                continue
            last = self._exec_buy(sym, quote_micros, price_i, sector, dry)
            actions.append(last)
            if last.get("ok"):
                spent = int(last.get("quote_usdt_micros") or quote_micros)
                est = apply_fee_bps(mul_div(spent, PRICE_SCALE, price_i), 30)
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
                        if int(fill.get("deal_funds_micros") or 0) > 0:
                            spent = int(fill["deal_funds_micros"])
                    else:
                        logger.warning(
                            "fill reconcile failed for %s order=%s — keeping estimate: %s",
                            sym,
                            order_id,
                            fill.get("message"),
                        )
                        size_basis = "estimated_fee_adjusted_unreconciled"
                with self._lock:
                    if equal_sleeves and sector:
                        if not self._debit_sleeve_locked(sector, spent):
                            # Should not happen after quote check; refuse to orphan bag.
                            logger.error(
                                "sleeve debit failed after buy %s sector=%s spent=%s",
                                sym,
                                sector,
                                spent,
                            )
                        self._save_state()
                    pos: Dict[str, Any] = {
                        "symbol": sym,
                        "entry_price_i": price_i,
                        "quote_usdt_micros": spent,
                        "base_size_i": size_i,
                        "size_basis": size_basis,
                        "sector": sector,
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
                    positions = dict(self._positions)
                    self._record_fire_locked(
                        sym, "buy", notional_micros=spent, is_buy=True
                    )
                buys += 1
                last["base_size_i"] = size_i
                last["size_basis"] = size_basis
                last["entry_price_i"] = price_i
                last["quote_usdt_micros"] = spent
                self._emit_action({**last, "source": "auto"})

        with self._lock:
            open_n = len(self._positions)
        summary = {
            "at": int(time.time()),
            "ok": True,
            "enabled": True,
            "auto_execute": True,
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
        """Sell-only on degraded snap: stop/TP via KuCoin mark; no buys / no fade."""
        dry = bool(cfg.get("dry_run"))
        stop_loss_bps = int(
            cfg.get("stop_loss_bps") or pct_to_bps(cfg.get("stop_loss_pct") or 12) or 1200
        )
        take_profit_bps = int(
            cfg.get("take_profit_bps")
            if cfg.get("take_profit_bps") is not None
            else (pct_to_bps(cfg.get("take_profit_pct") or 0) or 0)
        )
        trailing_stop_bps = int(cfg.get("trailing_stop_bps") or 0)
        trailing_offset_bps = int(cfg.get("trailing_offset_bps") or 0)
        max_hold_sec = int(cfg.get("max_hold_sec") or 0)
        roi_ladder = self._parse_roi_ladder(cfg)
        max_sells = int(
            cfg.get("max_sells_per_cycle")
            if cfg.get("max_sells_per_cycle") is not None
            else 5
        )
        actions: List[Dict[str, Any]] = []
        with self._lock:
            positions = dict(self._positions)
        sells = 0
        mark_miss = 0
        for sym, pos in list(positions.items()):
            if max_sells > 0 and sells >= max_sells:
                break
            entry_i = int(pos.get("entry_price_i") or 0)
            size_i = int(pos.get("base_size_i") or 0)
            if entry_i <= 0 or size_i <= 0:
                continue
            sell_reason, price_i, pos_updates = self._evaluate_sell_reason(
                sym,
                pos,
                row=None,
                sell_setups=set(),
                stop_loss_bps=stop_loss_bps,
                take_profit_bps=take_profit_bps,
                prefer_mark=True,
                trailing_stop_bps=trailing_stop_bps,
                trailing_offset_bps=trailing_offset_bps,
                fade_min_abs_bps=0,
                max_hold_sec=max_hold_sec,
                roi_ladder=roi_ladder,
            )
            if pos_updates:
                self._apply_pos_updates(sym, pos_updates)
            if not price_i:
                mark_miss += 1
                continue
            if not sell_reason:
                continue
            # Tag degraded path for audit honesty.
            if str(sell_reason).startswith("stop_loss"):
                sell_reason = f"stop_loss_degraded drop_bps={drop_bps(entry_i, int(price_i))} ({reason})"
            elif str(sell_reason).startswith("take_profit"):
                sell_reason = (
                    f"take_profit_degraded rise_bps={rise_bps(entry_i, int(price_i))} ({reason})"
                )
            elif str(sell_reason).startswith(
                ("trailing_stop", "roi_ladder", "max_hold")
            ):
                sell_reason = f"{sell_reason}_degraded ({reason})"
            result = self._exec_sell(
                sym, size_i, sell_reason, dry, mark_price_i=int(price_i)
            )
            actions.append(result)
            cost_m = int(
                pos.get("deal_funds_micros") or pos.get("quote_usdt_micros") or 0
            )
            proceeds_m = int(
                result.get("quote_usdt_micros")
                or result.get("deal_funds_micros")
                or 0
            )
            sell_ev: Dict[str, Any] = {
                **result,
                "source": "auto",
                "entry_price_i": pos.get("entry_price_i"),
                "exit_price_i": int(price_i),
                "base_size_i": int(result.get("base_size_i") or size_i),
                "cost_usdt_micros": cost_m if cost_m > 0 else None,
            }
            if proceeds_m > 0:
                sell_ev["quote_usdt_micros"] = proceeds_m
            if cost_m > 0 and proceeds_m > 0 and result.get("ok"):
                gross, fee_m, net = self._pnl_after_fees(cost_m, proceeds_m, cfg)
                sell_ev["realized_pnl_gross_usdt_micros"] = gross
                sell_ev["fee_usdt_micros"] = fee_m
                sell_ev["realized_pnl_usdt_micros"] = net
            self._emit_action(sell_ev)
            sells += 1
            if result.get("ok"):
                credit = self._sell_credit_micros(
                    pos,
                    result,
                    row={"price_i": price_i, "sector": pos.get("sector")},
                    dry=dry,
                )
                sector = str(pos.get("sector") or "")
                with self._lock:
                    self._positions.pop(sym, None)
                    self._save_positions_locked()
                    positions = dict(self._positions)
                    if self._sizing_equal(cfg) and sector and credit > 0:
                        self._credit_sleeve_locked(sector, credit)
                    pnl_net = sell_ev.get("realized_pnl_usdt_micros")
                    if isinstance(pnl_net, int):
                        self._note_sell_pnl_locked(pnl_net, cfg)
                    self._record_fire_locked(sym, "sell", notional_micros=0, is_buy=False)
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
        *,
        listings_soft_flags: set | None = None,
        soft_flags_by_sector: dict | None = None,
        symbol_blacklist: set | None = None,
        symbol_whitelist: set | None = None,
        exec_venues: set | None = None,
    ) -> List[Dict[str, Any]]:
        # Back-compat: listings_soft_flags alone still works.
        soft_map: Dict[str, set] = {}
        if soft_flags_by_sector:
            soft_map = {str(k): set(v or ()) for k, v in soft_flags_by_sector.items()}
        elif listings_soft_flags:
            soft_map["listings"] = set(listings_soft_flags)
        cfg = load_config().get("auto_trade") or {}
        if exec_venues is None:
            exec_venues = {
                str(v).strip().lower()
                for v in (cfg.get("exec_venues") or ["kucoin"])
                if v and str(v).strip()
            }
        if symbol_blacklist is None:
            blacklist = {
                str(s).upper().replace("-", "").replace("/", "")
                for s in (cfg.get("symbol_blacklist") or [])
                if s
            }
        else:
            blacklist = {
                str(s).upper().replace("-", "").replace("/", "")
                for s in symbol_blacklist
                if s
            }
        if symbol_whitelist is None:
            whitelist_raw = [
                str(s).upper().replace("-", "").replace("/", "")
                for s in (cfg.get("symbol_whitelist") or [])
                if s
            ]
            whitelist = set(whitelist_raw) if whitelist_raw else None
        else:
            whitelist = {
                str(s).upper().replace("-", "").replace("/", "")
                for s in symbol_whitelist
                if s
            } or None
        out: List[Dict[str, Any]] = []
        for sector in allowed:
            if sector in FUTURES_BLOCK:
                continue
            for row in sectors.get(sector) or []:
                if row.get("market") == "futures":
                    continue
                venue = str(row.get("venue") or "").strip().lower()
                if venue in {"dexscreener", "coingecko"}:
                    continue
                if exec_venues and venue and venue not in exec_venues:
                    continue
                sym = row.get("symbol")
                if not sym or sym in positions:
                    continue
                sym_u = str(sym).upper().replace("-", "").replace("/", "")
                if sym_u in blacklist:
                    continue
                if whitelist is not None and sym_u not in whitelist:
                    continue
                if row.get("setup") not in buy_setups:
                    continue
                if int(row.get("score_centi") or 0) < int(min_score_centi):
                    continue
                if require_clean:
                    flags = {str(f) for f in (row.get("risk_flags") or []) if f}
                    soft = soft_map.get(sector, set())
                    if _hard_risk_flags(flags, soft):
                        continue
                item = dict(row)
                item["sector"] = sector
                out.append(item)
        out.sort(key=lambda r: int(r.get("score_centi") or 0), reverse=True)
        return out

    @staticmethod
    def _parse_roi_ladder(cfg: Dict[str, Any]) -> List[Tuple[int, int]]:
        """Return ``[(after_sec, min_rise_bps), ...]`` sorted by after_sec ascending."""
        raw = cfg.get("roi_ladder")
        if not raw:
            return []
        if not isinstance(raw, list):
            return []
        out: List[Tuple[int, int]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                after = int(item.get("after_sec"))
                need = int(item.get("min_rise_bps"))
            except (TypeError, ValueError):
                continue
            if after < 0 or need < -10_000 or need > 50_000:
                continue
            out.append((after, need))
        out.sort(key=lambda x: x[0])
        return out

    @staticmethod
    def _roi_min_rise_bps(ladder: List[Tuple[int, int]], hold_sec: int) -> Optional[int]:
        """Best (latest) ladder step whose after_sec <= hold_sec."""
        if not ladder:
            return None
        chosen: Optional[int] = None
        for after, need in ladder:
            if hold_sec >= after:
                chosen = need
        return chosen

    def _evaluate_sell_reason(
        self,
        sym: str,
        pos: Dict[str, Any],
        *,
        row: Optional[Dict[str, Any]],
        sell_setups: set,
        stop_loss_bps: int,
        take_profit_bps: int,
        prefer_mark: bool,
        trailing_stop_bps: int = 0,
        trailing_offset_bps: int = 0,
        fade_min_abs_bps: int = 0,
        max_hold_sec: int = 0,
        roi_ladder: Optional[List[Tuple[int, int]]] = None,
        now_ms: Optional[int] = None,
    ) -> Tuple[Optional[str], int, Dict[str, Any]]:
        """Decide whether to sell an open bag. Always tries KuCoin mark when live.

        Returns ``(reason, mark_price_i, pos_updates)``.

        Priority: stop-loss → max-hold → trailing → take-profit → ROI ladder →
        board sell_setups (fade-watch, with min-move guard).
        """
        entry_i = int(pos.get("entry_price_i") or 0)
        mark_i = 0
        updates: Dict[str, Any] = {}
        if prefer_mark:
            try:
                mi = self._trader.get_spot_mark_price_i(str(sym))
                if mi and int(mi) > 0:
                    mark_i = int(mi)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "mark for sell eval failed %s: %s",
                    sym,
                    redact_exc(exc, max_len=96),
                )
        if mark_i <= 0 and row and int(row.get("price_i") or 0) > 0:
            mark_i = int(row["price_i"])

        ts = int(now_ms if now_ms is not None else time.time() * 1000)
        opened = int(pos.get("opened_at_ms") or 0)
        hold_sec = max(0, (ts - opened) // 1000) if opened > 0 else 0

        if entry_i > 0 and mark_i > 0:
            drop = drop_bps(entry_i, mark_i)
            if stop_loss_bps > 0 and drop >= int(stop_loss_bps):
                return f"stop_loss drop_bps={drop}", mark_i, updates

            if max_hold_sec > 0 and hold_sec >= int(max_hold_sec):
                return f"max_hold hold_sec={hold_sec}", mark_i, updates

            gain = rise_bps(entry_i, mark_i)

            # Trailing: arm after offset gain; track peak; sell on pullback from peak.
            trail_bps = int(trailing_stop_bps or 0)
            trail_off = int(trailing_offset_bps or 0)
            if trail_bps > 0 and trail_off > 0:
                peak = int(pos.get("peak_mark_price_i") or 0)
                armed = bool(pos.get("trailing_armed")) or gain >= trail_off
                if armed:
                    if not pos.get("trailing_armed"):
                        updates["trailing_armed"] = True
                    if mark_i > peak:
                        peak = mark_i
                        updates["peak_mark_price_i"] = peak
                    elif peak <= 0:
                        peak = mark_i
                        updates["peak_mark_price_i"] = peak
                    if peak > 0:
                        pull = drop_bps(peak, mark_i)
                        if pull >= trail_bps:
                            return (
                                f"trailing_stop pull_bps={pull} peak={peak}",
                                mark_i,
                                updates,
                            )
                elif mark_i > peak:
                    # Still track peak before arm for honesty (optional).
                    updates["peak_mark_price_i"] = mark_i

            if take_profit_bps > 0 and gain >= int(take_profit_bps):
                return f"take_profit rise_bps={gain}", mark_i, updates

            ladder = roi_ladder if roi_ladder is not None else []
            need = self._roi_min_rise_bps(ladder, hold_sec)
            if need is not None and gain >= int(need):
                return (
                    f"roi_ladder hold_sec={hold_sec} need_bps={need} rise_bps={gain}",
                    mark_i,
                    updates,
                )

        if row:
            setup = row.get("setup")
            if setup in sell_setups:
                # Guard: ignore fade/setup noise when |move| from entry is tiny (fee drag).
                min_abs = int(fade_min_abs_bps or 0)
                if min_abs > 0 and entry_i > 0 and mark_i > 0:
                    move = abs(int(rise_bps(entry_i, mark_i)))
                    if move < min_abs:
                        return None, mark_i, updates
                return f"setup={setup}", mark_i, updates
        return None, mark_i, updates

    def _apply_pos_updates(self, sym: str, updates: Dict[str, Any]) -> None:
        if not updates:
            return
        want = self._norm_symbol(sym)
        with self._lock:
            cur = self._positions.get(want)
            if not isinstance(cur, dict):
                return
            changed = False
            for k, v in updates.items():
                if cur.get(k) != v:
                    cur[k] = v
                    changed = True
            if changed:
                self._positions[want] = cur
                self._save_positions_locked()

    def _find_row(
        self, sectors: Dict[str, Any], allowed: List[str], symbol: str
    ) -> Optional[Dict[str, Any]]:
        want = self._norm_symbol(symbol)
        for sector in allowed:
            for row in sectors.get(sector) or []:
                if self._norm_symbol(str(row.get("symbol") or "")) == want:
                    out = dict(row)
                    out["sector"] = sector
                    return out
        return None

    def _exec_buy(
        self, symbol: str, quote_micros: int, price_i: int, sector: Any, dry: bool
    ) -> Dict[str, Any]:
        if dry:
            msg = f"DRY buy {symbol} micros={quote_micros} @price_i={price_i}"
            logger.info(msg)
            return {
                "ok": True,
                "dry_run": True,
                "side": "buy",
                "symbol": symbol,
                "message": msg,
                "quote_usdt_micros": int(quote_micros),
                "entry_price_i": int(price_i),
            }
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
            "quote_usdt_micros": int(quote_micros),
        }
        if oid:
            out["order_id"] = oid
        return out

    def _sizing_equal(self, cfg: Dict[str, Any]) -> bool:
        mode = str(cfg.get("sizing_mode") or _SIZING_FIXED).strip().lower()
        return mode == _SIZING_EQUAL

    def _sizing_shared(self, cfg: Dict[str, Any]) -> bool:
        mode = str(cfg.get("sizing_mode") or _SIZING_FIXED).strip().lower()
        return mode in {_SIZING_SHARED, "shared", "pool"}

    def _shared_pool_buy_quote(
        self,
        cfg: Dict[str, Any],
        *,
        open_n: int,
        max_open: int,
    ) -> Tuple[int, Optional[str]]:
        """Size one buy from free USDT: min(per-buy cap, fair share of remaining slots)."""
        free, err = self._read_free_usdt_micros(cfg)
        if err:
            return 0, err
        reserve = int(cfg.get("shared_pool_reserve_usdt_micros") or 0)
        if reserve < 0:
            reserve = 0
        usable = int(free) - int(reserve)
        if usable < _MIN_SHARED_BUY_MICROS:
            return 0, f"shared pool usable={usable} < 1 USDT"
        cap = int(
            cfg.get("quote_usdt_micros_per_buy")
            or quote_i(cfg.get("quote_usdt_per_buy") or 15)
            or (15 * QUOTE_SCALE)
        )
        if cap <= 0:
            return 0, "quote_usdt_micros_per_buy must be > 0"
        max_q = int(
            (load_config().get("api") or {}).get("max_order_quote_usdt_micros")
            or 500_000_000
        )
        slots = max(1, int(max_open) - int(open_n))
        fair = usable // slots
        quote = min(int(cap), int(fair), int(usable), int(max_q))
        if quote < _MIN_SHARED_BUY_MICROS:
            return 0, f"shared pool quote={quote} < 1 USDT"
        return int(quote), None

    def _sleeve_sectors(self, cfg: Dict[str, Any]) -> List[str]:
        raw = [str(s) for s in (cfg.get("allowed_sectors") or []) if s]
        return [s for s in raw if s not in FUTURES_BLOCK]

    def _sector_has_open(self, sector: str, positions: Dict[str, Any]) -> bool:
        if not sector:
            return False
        for pos in positions.values():
            if not isinstance(pos, dict):
                continue
            if str(pos.get("sector") or "") == sector:
                return True
        return False

    def _read_free_usdt_micros(self, cfg: Dict[str, Any]) -> Tuple[int, Optional[str]]:
        """Live: KuCoin trade USDT. Dry-run / no keys: paper seed from config."""
        dry = bool(cfg.get("dry_run"))
        paper = int(
            cfg.get("sleeve_paper_seed_usdt_micros")
            if cfg.get("sleeve_paper_seed_usdt_micros") is not None
            else _DEFAULT_PAPER_SEED_MICROS
        )
        if dry or not self._trader.ready:
            if paper <= 0:
                return 0, "sleeve_paper_seed_usdt_micros must be > 0 for dry seed"
            return paper, None
        try:
            base_i = int(self._trader.get_available_base_i("USDT"))
        except Exception as exc:  # noqa: BLE001
            return 0, redact_exc(exc, max_len=180)
        micros = usdt_base_i_to_micros(base_i)
        if micros <= 0:
            return 0, "KuCoin trade USDT available is 0"
        return micros, None

    def _ensure_sleeves_locked(
        self, cfg: Dict[str, Any], *, force: bool = False
    ) -> Optional[str]:
        """Caller holds lock. Seed equal sleeves once (or force reseed)."""
        if not self._sizing_equal(cfg):
            return None
        sectors = self._sleeve_sectors(cfg)
        if not sectors:
            return "no allowed_sectors for sleeves"
        cur = self._state.get("sleeves")
        if (
            not force
            and isinstance(cur, dict)
            and all(isinstance(cur.get(s), int) for s in sectors)
            and any(int(cur.get(s) or 0) > 0 for s in sectors)
        ):
            # Keep existing sleeve books; add missing sector keys at 0.
            for s in sectors:
                if s not in cur:
                    cur[s] = 0
            self._state["sleeves"] = cur
            return None
        total, err = self._read_free_usdt_micros(cfg)
        if err:
            return err
        parts = split_equal_micros(total, len(sectors))
        if len(parts) != len(sectors):
            return "equal sleeve split failed"
        sleeves = {sectors[i]: int(parts[i]) for i in range(len(sectors))}
        self._state["sleeves"] = sleeves
        self._state["sleeves_seed_usdt_micros"] = int(total)
        self._state["sleeves_seeded_at"] = int(time.time())
        logger.info(
            "equal sleeves seeded total_micros=%s parts=%s",
            total,
            {k: sleeves[k] for k in sectors},
        )
        return None

    def _sleeve_buy_quote(
        self, sector: str, cfg: Dict[str, Any]
    ) -> Tuple[int, Optional[str]]:
        if not sector or sector in FUTURES_BLOCK:
            return 0, "invalid sleeve sector"
        with self._lock:
            sleeves = self._state.get("sleeves")
            if not isinstance(sleeves, dict):
                return 0, "sleeves not seeded"
            cash = int(sleeves.get(sector) or 0)
        if cash <= 0:
            return 0, f"sleeve {sector} cash=0"
        max_q = int(
            (load_config().get("api") or {}).get("max_order_quote_usdt_micros")
            or 500_000_000
        )
        quote = min(cash, max_q)
        if quote <= 0:
            return 0, f"sleeve {sector} quote after max_order cap=0"
        return quote, None

    def _debit_sleeve_locked(self, sector: str, micros: int) -> bool:
        sleeves = self._state.get("sleeves")
        if not isinstance(sleeves, dict):
            return False
        cash = int(sleeves.get(sector) or 0)
        spend = int(micros)
        if spend <= 0 or spend > cash:
            return False
        sleeves[sector] = cash - spend
        self._state["sleeves"] = sleeves
        self._state["updated_at"] = int(time.time())
        return True

    def _credit_sleeve_locked(self, sector: str, micros: int) -> None:
        if not sector or int(micros) <= 0:
            return
        sleeves = self._state.get("sleeves")
        if not isinstance(sleeves, dict):
            sleeves = {}
        sleeves[sector] = int(sleeves.get(sector) or 0) + int(micros)
        self._state["sleeves"] = sleeves
        self._state["updated_at"] = int(time.time())

    def _sell_credit_micros(
        self,
        pos: Dict[str, Any],
        result: Dict[str, Any],
        *,
        row: Optional[Dict[str, Any]],
        dry: bool,
    ) -> int:
        """USDT micros returned to the sleeve after a successful sell."""
        for key in ("deal_funds_micros", "quote_usdt_micros"):
            v = result.get(key)
            if isinstance(v, int) and not isinstance(v, bool) and v > 0:
                return int(v)
        if dry or result.get("dry_run"):
            price_i = 0
            if row and int(row.get("price_i") or 0) > 0:
                price_i = int(row["price_i"])
            size_i = int(pos.get("base_size_i") or 0)
            if price_i > 0 and size_i > 0:
                return int(mul_div(size_i, price_i, PRICE_SCALE) or 0)
            return int(pos.get("quote_usdt_micros") or 0)
        # Live without fill funds yet — fall back to cost basis (no invented profit).
        return int(pos.get("deal_funds_micros") or pos.get("quote_usdt_micros") or 0)

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

    def _exec_sell(
        self,
        symbol: str,
        size_i: int,
        reason: str,
        dry: bool,
        *,
        mark_price_i: Optional[int] = None,
    ) -> Dict[str, Any]:
        if dry:
            msg = f"DRY sell {symbol} base_size_i={size_i} reason={reason}"
            logger.info(msg)
            out_dry: Dict[str, Any] = {
                "ok": True,
                "dry_run": True,
                "side": "sell",
                "symbol": symbol,
                "message": msg,
                "reason": reason,
                "base_size_i": int(size_i),
            }
            if mark_price_i and int(mark_price_i) > 0 and int(size_i) > 0:
                q = int(notional_i_from_price_qty(int(mark_price_i), int(size_i)))
                out_dry["quote_usdt_micros"] = q
                out_dry["exit_price_i"] = int(mark_price_i)
                out_dry["size_basis"] = "estimated_mark"
            return out_dry
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
        oid = extract_order_id(result.exchange)
        out_sell: Dict[str, Any] = {
            "ok": result.ok,
            "dry_run": result.dry_run,
            "side": "sell",
            "symbol": symbol,
            "message": result.message,
            "reason": reason,
            "exchange": sanitize_exchange_surface(result.exchange),
            "client_oid": client_oid,
            "base_size_i": int(size_i),
        }
        if oid:
            out_sell["order_id"] = oid
        if result.ok and oid:
            cfg = load_config().get("auto_trade") or {}
            if bool(cfg.get("fill_reconcile", True)):
                fill = self._reconcile_buy_fill(symbol, str(oid), cfg)
                if int(fill.get("deal_funds_micros") or 0) > 0:
                    funds = int(fill["deal_funds_micros"])
                    out_sell["deal_funds_micros"] = funds
                    out_sell["quote_usdt_micros"] = funds
                if int(fill.get("base_size_i") or 0) > 0:
                    out_sell["base_size_i"] = int(fill["base_size_i"])
                    out_sell["size_basis"] = str(fill.get("size_basis") or "kucoin_fill")
                qty = int(out_sell.get("base_size_i") or 0)
                funds_m = int(out_sell.get("deal_funds_micros") or 0)
                if qty > 0 and funds_m > 0:
                    # inverse of notional_i_from_price_qty: price = quote * 1e10 / qty
                    out_sell["exit_price_i"] = int(mul_div(funds_m, 10_000_000_000, qty))
        if (
            result.ok
            and not int(out_sell.get("quote_usdt_micros") or 0)
            and mark_price_i
            and int(mark_price_i) > 0
            and int(size_i) > 0
        ):
            # Fill poll missed funds — honest mark estimate (not invented edge).
            out_sell["quote_usdt_micros"] = int(
                notional_i_from_price_qty(int(mark_price_i), int(size_i))
            )
            out_sell["exit_price_i"] = int(mark_price_i)
            out_sell["size_basis"] = str(out_sell.get("size_basis") or "estimated_mark")
        return out_sell

    def _ensure_daily_locked(self) -> Dict[str, Any]:
        """Reset UTC-day counters when the day rolls. Caller holds lock."""
        day = _utc_day()
        daily = self._state.get("daily")
        if not isinstance(daily, dict) or daily.get("day_utc") != day:
            daily = {
                "day_utc": day,
                "buys": 0,
                "sells": 0,
                "buy_notional_micros": 0,
            }
            self._state["daily"] = daily
        return daily

    def _daily_snapshot_locked(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        daily = dict(self._ensure_daily_locked())
        daily["max_buys"] = int(
            cfg.get("max_daily_buys")
            if cfg.get("max_daily_buys") is not None
            else _DEFAULT_MAX_DAILY_BUYS
        )
        daily["max_notional_usdt_micros"] = int(
            cfg.get("max_daily_notional_usdt_micros")
            if cfg.get("max_daily_notional_usdt_micros") is not None
            else _DEFAULT_MAX_DAILY_NOTIONAL_MICROS
        )
        return daily

    def _cooldown_blocked(
        self, symbol: str, side: str, cooldown_sec: int
    ) -> Tuple[bool, str]:
        if cooldown_sec <= 0:
            return False, ""
        key = _fire_key(symbol, side)
        with self._lock:
            last_fire = self._state.get("last_fire")
            if not isinstance(last_fire, dict):
                return False, ""
            last_ts = int(last_fire.get(key) or 0)
        if last_ts <= 0:
            return False, ""
        age = int(time.time()) - last_ts
        if age < cooldown_sec:
            remain = cooldown_sec - age
            return True, f"signal cooldown {remain}s left for {key}"
        return False, ""

    @staticmethod
    def _pnl_after_fees(
        cost_m: int, proceeds_m: int, cfg: Dict[str, Any]
    ) -> Tuple[int, int, int]:
        """Gross PnL, estimated round-trip fee micros, net PnL (integer)."""
        gross = int(proceeds_m) - int(cost_m)
        fee_side = int(cfg.get("fee_bps_per_side") or 0)
        if fee_side <= 0:
            return gross, 0, gross
        fee_m = mul_div(int(cost_m), fee_side, 10_000) + mul_div(
            int(proceeds_m), fee_side, 10_000
        )
        return gross, int(fee_m), int(gross) - int(fee_m)

    def _note_sell_pnl_locked(self, pnl_net: int, cfg: Dict[str, Any]) -> None:
        """Track consecutive losing sells; pause new buys after streak (exits continue)."""
        need = int(cfg.get("loss_streak_pause_buys") or 0)
        if pnl_net >= 0:
            self._state["loss_streak"] = 0
            return
        streak = int(self._state.get("loss_streak") or 0) + 1
        self._state["loss_streak"] = streak
        if need > 0 and streak >= need:
            pause = int(cfg.get("loss_streak_pause_sec") or 3600)
            until = int(time.time()) + max(60, pause)
            self._state["pause_buys_until"] = until
            logger.warning(
                "loss streak=%s — pause buys until %s (%ss)",
                streak,
                until,
                pause,
            )

    def _buys_paused(self) -> Tuple[bool, str]:
        with self._lock:
            until = int(self._state.get("pause_buys_until") or 0)
        if until <= 0:
            return False, ""
        now = int(time.time())
        if now >= until:
            return False, ""
        return True, f"loss_streak pause buys {until - now}s left"

    def _buy_gates_ok(
        self,
        symbol: str,
        quote_micros: int,
        *,
        max_daily_buys: int,
        max_daily_notional: int,
        cooldown_sec: int,
    ) -> Tuple[bool, str]:
        paused, pause_msg = self._buys_paused()
        if paused:
            return False, pause_msg
        blocked, msg = self._cooldown_blocked(symbol, "buy", cooldown_sec)
        if blocked:
            return False, msg
        with self._lock:
            daily = self._ensure_daily_locked()
            buys = int(daily.get("buys") or 0)
            notional = int(daily.get("buy_notional_micros") or 0)
        if max_daily_buys > 0 and buys >= max_daily_buys:
            return False, f"max_daily_buys={max_daily_buys} reached"
        if (
            max_daily_notional > 0
            and (notional + int(quote_micros)) > max_daily_notional
        ):
            return (
                False,
                f"max_daily_notional_usdt_micros={max_daily_notional} would exceed",
            )
        return True, ""

    def _record_fire_locked(
        self,
        symbol: str,
        side: str,
        *,
        notional_micros: int,
        is_buy: bool,
    ) -> None:
        """Caller holds lock. Updates cooldown + daily counters after success."""
        key = _fire_key(symbol, side)
        last_fire = self._state.get("last_fire")
        if not isinstance(last_fire, dict):
            last_fire = {}
            self._state["last_fire"] = last_fire
        last_fire[key] = int(time.time())
        # Bound map size (soft) — drop oldest-ish by keeping last 500 keys
        if len(last_fire) > 500:
            for old in list(last_fire.keys())[: len(last_fire) - 500]:
                last_fire.pop(old, None)
        daily = self._ensure_daily_locked()
        if is_buy:
            daily["buys"] = int(daily.get("buys") or 0) + 1
            daily["buy_notional_micros"] = int(daily.get("buy_notional_micros") or 0) + int(
                notional_micros
            )
        else:
            daily["sells"] = int(daily.get("sells") or 0) + 1
        self._state["updated_at"] = int(time.time())
        self._save_state()

    def _load_state(self) -> Dict[str, Any]:
        cfg = load_config().get("auto_trade") or {}
        default: Dict[str, Any] = {
            "enabled": bool(cfg.get("enabled_default")),
            "kill_switch": False,
            "kill_reason": None,
            "kill_switch_at": 0,
            "updated_at": 0,
            "daily": {
                "day_utc": _utc_day(),
                "buys": 0,
                "sells": 0,
                "buy_notional_micros": 0,
            },
            "last_fire": {},
        }
        data, err, _meta = read_json_object(STATE_PATH)
        if err:
            logger.warning(
                "auto state unreadable — using defaults: %s",
                redact_text(str(err))[:256],
            )
            return default
        merged = {**default, **data}
        # Never inherit enabled=true from a corrupted partial without kill fields
        if "kill_switch" not in data:
            merged["kill_switch"] = False
        if not isinstance(merged.get("last_fire"), dict):
            merged["last_fire"] = {}
        if not isinstance(merged.get("daily"), dict):
            merged["daily"] = default["daily"]
        return merged

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

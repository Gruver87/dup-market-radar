"""Auto buy/sell engine — off by default; never trades futures.

Runtime toggle ``enabled`` is the opt-in **auto_execute** / autonomous mode.
When armed (enabled + keys + dry_run=false), qualifying buy-watch / new-listing
buys and fade-watch / stop-loss sells place KuCoin spot market orders without a
browser confirm dialog. Manual Buy/Sell buttons keep their own preview/confirm.
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

# Conservative lab defaults when config omits rails.
_DEFAULT_MAX_DAILY_BUYS = 20
_DEFAULT_MAX_DAILY_NOTIONAL_MICROS = 200 * QUOTE_SCALE  # 200 USDT
_DEFAULT_SIGNAL_COOLDOWN_SEC = 900  # 15m same symbol+side
_DEFAULT_PAPER_SEED_MICROS = 90 * QUOTE_SCALE  # dry-run seed when no KuCoin USDT
_SIZING_EQUAL = "equal_sleeves"
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
            }

    def loop_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

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
        fixed_quote_micros = int(
            cfg.get("quote_usdt_micros_per_buy")
            or quote_i(cfg.get("quote_usdt_per_buy") or 10)
            or (10 * QUOTE_SCALE)
        )
        equal_sleeves = self._sizing_equal(cfg)
        max_open = int(cfg.get("max_open_positions") or 5)
        max_buys = int(cfg.get("max_buys_per_cycle") or 2)
        max_sells = int(cfg.get("max_sells_per_cycle") or 5)
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
        # Triggers: sell_setups (default fade-watch) OR stop_loss_bps from entry.
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
            # Fade/setup sells respect cooldown; hard stop-loss always may fire.
            is_stop = str(reason).startswith("stop_loss")
            if not is_stop:
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
            result = self._exec_sell(sym, size_i, reason, dry)
            actions.append(result)
            self._emit_action({**result, "source": "auto", "entry_price_i": pos.get("entry_price_i")})
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
                        self._save_state()
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
            },
        )
        for row in candidates:
            if buys >= max_buys or open_n >= max_open:
                break
            sym = row["symbol"]
            price_i = int(row.get("price_i") or 0)
            sector = str(row.get("sector") or "")
            if price_i <= 0:
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
                        self._save_state()
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
    ) -> List[Dict[str, Any]]:
        # Back-compat: listings_soft_flags alone still works.
        soft_map: Dict[str, set] = {}
        if soft_flags_by_sector:
            soft_map = {str(k): set(v or ()) for k, v in soft_flags_by_sector.items()}
        elif listings_soft_flags:
            soft_map["listings"] = set(listings_soft_flags)
        cfg = load_config().get("auto_trade") or {}
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
                if row.get("venue") in {"dexscreener", "coingecko"}:
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
                    hard = flags - soft
                    if hard:
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
        }
        if oid:
            out_sell["order_id"] = oid
        if result.ok and oid:
            cfg = load_config().get("auto_trade") or {}
            if bool(cfg.get("fill_reconcile", True)):
                fill = self._reconcile_buy_fill(symbol, str(oid), cfg)
                if int(fill.get("deal_funds_micros") or 0) > 0:
                    out_sell["deal_funds_micros"] = int(fill["deal_funds_micros"])
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

    def _buy_gates_ok(
        self,
        symbol: str,
        quote_micros: int,
        *,
        max_daily_buys: int,
        max_daily_notional: int,
        cooldown_sec: int,
    ) -> Tuple[bool, str]:
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

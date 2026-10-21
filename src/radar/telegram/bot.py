"""Telegram bot — commands, alert loop, nearest entry/exit. No futures auto-orders."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from radar.config import ROOT, load_config
from radar.html_escape import escape_html
from radar.i18n import t
from radar.money import display_price, price_i, quote_i
from radar.ops.fx import FxError, convert, format_fx_html, parse_amount_to_micros
from radar.ops.world_time import format_world_clock_html
from radar.prefs import PrefsStore
from radar.rate_limit import RateLimiter
from radar.secrets_redact import redact_exc
from radar.telegram.alerts import find_row_public, format_alert, scan_all
from radar.telegram.client import TelegramClient, TelegramError
from radar.telegram.env import (
    telegram_allowed_chat_ids,
    telegram_bot_token,
    telegram_enabled_env,
)
from radar.telegram.nearest import nearest_for_action
from radar.telegram.stats import TG_STATS
from radar.telegram.watches import WatchBook, WatchBookFull, norm_symbol
from radar.trade.symbol_validate import (
    SymbolValidationError,
    normalize_trade_symbol,
)

logger = logging.getLogger("radar.telegram.bot")


class TelegramBot:
    def __init__(
        self,
        snapshot_fn: Callable[[], Dict[str, Any]],
        *,
        auto_status_fn: Optional[Callable[[], Dict[str, Any]]] = None,
        trade_place_fn: Optional[Callable[..., Any]] = None,
        mark_price_fn: Optional[Callable[[str], Optional[int]]] = None,
    ) -> None:
        self._snapshot_fn = snapshot_fn
        self._auto_status_fn = auto_status_fn
        self._trade_place_fn = trade_place_fn
        self._mark_price_fn = mark_price_fn
        self._stop = threading.Event()
        self._poll_thread: Optional[threading.Thread] = None
        self._alert_thread: Optional[threading.Thread] = None
        self._offset: Optional[int] = None
        self._client: Optional[TelegramClient] = None
        tg_cfg = (load_config().get("telegram") or {})
        self._cmd_limiter = RateLimiter(
            max_per_window=int(tg_cfg.get("cmd_rate_limit_per_min") or 30),
            window_ms=60_000,
        )
        max_watches = int(tg_cfg.get("max_watches") or 2_000)
        self._book = WatchBook(
            ROOT / "data" / "telegram_watches.json",
            max_watches=max_watches,
        )
        self._prefs = PrefsStore(ROOT / "data" / "tg_prefs.json")
        self._allowed: List[int] = []

    def configured(self) -> bool:
        return bool(telegram_bot_token() and telegram_allowed_chat_ids())

    def status(self) -> Dict[str, Any]:
        cfg = (load_config().get("telegram") or {})
        out: Dict[str, Any] = {
            "enabled_cfg": bool(cfg.get("enabled", True)),
            "env_ready": self.configured(),
            "running": bool(
                self._poll_thread and self._poll_thread.is_alive()
            ),
            "allowed_chats": len(self._allowed),
            "watches": self._book.status(),
            "prefs": self._prefs.status(),
        }
        if self._client is not None:
            out["circuit"] = self._client.circuit_status()
        out["stats"] = TG_STATS.snapshot()
        return out

    def start(self) -> bool:
        cfg = load_config().get("telegram") or {}
        if cfg.get("enabled") is False:
            logger.info("telegram.enabled=false — skip")
            return False
        # Soft-skip without secrets (fail-open for radar process, fail-closed for bot)
        if not telegram_enabled_env() and not self.configured():
            logger.info("telegram: no TELEGRAM_BOT_TOKEN / chat ids — skip")
            return False
        token = telegram_bot_token()
        self._allowed = telegram_allowed_chat_ids()
        if not token or not self._allowed:
            logger.warning("telegram token or TELEGRAM_ALLOWED_CHAT_IDS missing")
            return False
        self._client = TelegramClient(
            token,
            fail_threshold=int(cfg.get("circuit_fail_threshold") or 3),
            open_ms=int(cfg.get("circuit_open_ms") or 60_000),
        )
        self._stop.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop, name="radar-tg-poll", daemon=True
        )
        self._alert_thread = threading.Thread(
            target=self._alert_loop, name="radar-tg-alerts", daemon=True
        )
        self._poll_thread.start()
        self._alert_thread.start()
        logger.info("telegram bot started chats=%s", len(self._allowed))
        return True

    def stop(self, *, join_timeout_sec: float = 5.0) -> None:
        self._stop.set()
        for thr in (self._poll_thread, self._alert_thread):
            if thr and thr.is_alive():
                thr.join(timeout=join_timeout_sec)
        self._poll_thread = None
        self._alert_thread = None
        logger.info("telegram bot stopped")

    def broadcast(self, text: str) -> None:
        if not self._client:
            return
        for chat_id in self._allowed:
            try:
                self._client.send_message(chat_id, text)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "broadcast failed chat=%s err=%s",
                    chat_id,
                    redact_exc(exc, max_len=256),
                )

    def notify_trade(self, event: Dict[str, Any]) -> None:
        """Optional hook from auto/manual trade."""
        cfg = load_config().get("telegram") or {}
        if not cfg.get("notify_trades", True):
            return
        side = escape_html(event.get("side"))
        sym = escape_html(event.get("symbol"))
        src = escape_html(event.get("source") or "manual")
        trade_msg = str(event.get("message") or "")[:512]
        msg = (
            f"📤 <b>Trade</b> [{src}] {side} <code>{sym}</code>\n"
            f"{escape_html(trade_msg)}\n"
            f"ok={escape_html(event.get('ok'))} dry={escape_html(event.get('dry_run'))}"
        )
        if event.get("size_basis"):
            msg += (
                f"\nsize_basis={escape_html(event.get('size_basis'))} "
                f"base_size_i={escape_html(event.get('base_size_i'))}"
            )
        if event.get("reason"):
            msg += f"\nreason={escape_html(event.get('reason'))}"
        self.broadcast(msg)

    def watch_auto_spot(self, symbol: str, entry_price_i: Optional[int] = None) -> int:
        """After auto/manual live buy — open spot watches for all allow-listed chats."""
        if not self._allowed:
            return 0
        n = 0
        for chat_id in self._allowed:
            try:
                if self._book.ensure(
                    chat_id,
                    symbol,
                    market="spot",
                    entry_price_i=entry_price_i,
                    note="auto-buy",
                ):
                    n += 1
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "watch_auto_spot failed chat=%s err=%s",
                    chat_id,
                    redact_exc(exc, max_len=256),
                )
        return n

    def unwatch_spot(self, symbol: str) -> int:
        """After successful spot sell — drop spot watches for all allow-listed chats."""
        if not self._allowed or not symbol:
            return 0
        n = 0
        for chat_id in self._allowed:
            try:
                n += self._book.remove(chat_id, symbol, market="spot")
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "unwatch_spot failed chat=%s err=%s",
                    chat_id,
                    redact_exc(exc, max_len=256),
                )
        if n:
            logger.info("unwatch_spot %s removed=%s", symbol, n)
        return n

    def prune_watches(self) -> int:
        cfg = load_config().get("telegram") or {}
        ttl = int(cfg.get("watch_ttl_sec") or 0)
        return self._book.prune_stale(ttl)

    def sync_auto_positions(self, positions: Dict[str, Any]) -> int:
        """Ensure spot watches exist for current auto bags (all allow-listed chats)."""
        if not self._allowed or not positions:
            return 0
        added = 0
        for sym, pos in positions.items():
            if not isinstance(pos, dict):
                continue
            entry = pos.get("entry_price_i")
            entry_i = int(entry) if entry else None
            for chat_id in self._allowed:
                try:
                    if self._book.ensure(
                        chat_id,
                        str(sym),
                        market="spot",
                        entry_price_i=entry_i,
                        note="auto-sync",
                    ):
                        added += 1
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "sync_auto_positions failed: %s",
                        redact_exc(exc, max_len=256),
                    )
        return added

    def _authorized(self, chat_id: int) -> bool:
        return int(chat_id) in self._allowed

    def _send(self, chat_id: int, text: str) -> None:
        if not self._client:
            return
        try:
            self._client.send_message(chat_id, text)
        except TelegramError as exc:
            # No exc_info — Bot API URL embeds token
            logger.error("send failed: %s", redact_exc(exc, max_len=256))

    def _poll_loop(self) -> None:
        assert self._client is not None
        while not self._stop.is_set():
            try:
                updates = self._client.get_updates(offset=self._offset, timeout=25)
            except Exception as exc:  # noqa: BLE001
                logger.error("getUpdates failed: %s", redact_exc(exc, max_len=256))
                self._stop.wait(3)
                continue
            for upd in updates:
                uid = upd.get("update_id")
                if isinstance(uid, int):
                    self._offset = uid + 1
                msg = upd.get("message") or {}
                chat = (msg.get("chat") or {}).get("id")
                text = (msg.get("text") or "").strip()
                if chat is None or not text:
                    continue
                if len(text) > 256:
                    logger.warning("refuse oversized telegram text chat=%s", chat)
                    continue
                if not self._authorized(int(chat)):
                    logger.warning("refuse unauthorized chat_id=%s", chat)
                    continue
                try:
                    self._handle(int(chat), text)
                except Exception as exc:  # noqa: BLE001
                    logger.error("handler failed: %s", redact_exc(exc, max_len=256))
                    self._send(int(chat), "ошибка обработчика — см. логи radar")

    def _alert_loop(self) -> None:
        while not self._stop.is_set():
            cfg = load_config().get("telegram") or {}
            cycle = int(cfg.get("alert_cycle_sec") or 60)
            try:
                self.prune_watches()
                self._run_alerts()
            except Exception as exc:  # noqa: BLE001
                logger.error("alert cycle failed: %s", redact_exc(exc, max_len=256))
            self._stop.wait(cycle)

    def _run_alerts(self) -> None:
        cfg = load_config().get("telegram") or {}
        auto_cfg = load_config().get("auto_trade") or {}
        snap = self._snapshot_fn()
        if not snap:
            TG_STATS.inc("alerts_skipped_empty")
            return
        if snap.get("ok") is False:
            logger.warning("telegram alerts skipped — snapshot ok=false")
            TG_STATS.inc("alerts_skipped_degraded")
            return
        max_age = int(cfg.get("snapshot_max_age_ms") or 180_000)
        if max_age > 0:
            gen = snap.get("generated_at_ms")
            if gen is None and snap.get("generated_at") is not None:
                try:
                    # Prefer ms; legacy float/int epoch seconds → ms
                    raw = float(snap["generated_at"])
                    gen = int(raw) if raw > 1e12 else int(raw * 1000)
                except (TypeError, ValueError):
                    gen = None
            if gen is not None:
                age = int(time.time() * 1000) - int(gen)
                if age > max_age:
                    logger.warning(
                        "telegram alerts skipped — snapshot stale age_ms=%s max=%s",
                        age,
                        max_age,
                    )
                    TG_STATS.inc("alerts_skipped_stale")
                    return
        mark_fn = None
        if bool(cfg.get("outside_top_mark_alerts", True)):
            mark_fn = self._mark_price_fn
        alerts = scan_all(

            self._book,
            snap,
            stop_loss_bps=int(
                cfg.get("stop_loss_bps")
                or auto_cfg.get("stop_loss_bps")
                or 1200
            ),
            take_profit_warn_bps=int(cfg.get("take_profit_warn_bps") or 800),
            alert_cooldown_ms=int(cfg.get("alert_cooldown_ms") or 300_000),
            mark_price_fn=mark_fn,
        )
        for alert in alerts:
            chat_id = int(alert.get("chat_id") or 0)
            if not self._authorized(chat_id):
                continue
            self._send(chat_id, format_alert(alert))
            TG_STATS.inc("alerts_sent")
            self._book.touch_alert(
                chat_id,
                str(alert.get("symbol")),
                str(alert.get("market")),
                str(alert.get("alert_key")),
            )

    def _lang(self, chat_id: int):
        return self._prefs.lang_for(chat_id)

    def _handle(self, chat_id: int, text: str) -> None:
        lang = self._lang(chat_id)
        allowed, retry_after = self._cmd_limiter.check(str(int(chat_id)))
        if not allowed:
            TG_STATS.inc("cmd_rate_refused")
            self._send(chat_id, t("cmd_rate", lang, sec=retry_after))
            return
        parts = text.split()
        cmd = parts[0].split("@")[0].lower()
        args = parts[1:]

        if cmd in {"/start", "/help"}:
            self._send(chat_id, t("help", lang))
            return
        if cmd == "/lang":
            self._cmd_lang(chat_id, args)
            return
        if cmd == "/time":
            self._send(chat_id, format_world_clock_html(lang=lang))
            return
        if cmd == "/fx":
            self._cmd_fx(chat_id, args)
            return
        if cmd == "/bubbles":
            self._cmd_bubbles(chat_id)
            return
        if cmd == "/status":
            self._cmd_status(chat_id)
            return
        if cmd == "/top":
            self._cmd_top(chat_id, args)
            return
        if cmd == "/watch":
            self._cmd_watch(chat_id, args)
            return
        if cmd == "/unwatch":
            self._cmd_unwatch(chat_id, args)
            return
        if cmd == "/positions":
            self._cmd_positions(chat_id)
            return
        if cmd in {"/alerts", "/alert"}:
            self._run_alerts()
            self._send(chat_id, t("alerts_done", lang))
            return
        if cmd == "/nearest":
            self._cmd_nearest(chat_id, args)
            return
        if cmd == "/quote":
            self._cmd_quote(chat_id, args)
            return
        if cmd == "/buy":
            self._cmd_trade(chat_id, "buy", args)
            return
        if cmd == "/sell":
            self._cmd_trade(chat_id, "sell", args)
            return
        self._send(chat_id, t("unknown_cmd", lang))

    def _cmd_lang(self, chat_id: int, args: List[str]) -> None:
        if not args:
            self._send(chat_id, t("lang_usage", self._lang(chat_id)))
            return
        lang = self._prefs.set_lang(chat_id, args[0])
        self._send(chat_id, t("lang_set", lang))

    def _cmd_fx(self, chat_id: int, args: List[str]) -> None:
        lang = self._lang(chat_id)
        if len(args) < 3:
            self._send(chat_id, t("fx_usage", lang))
            return
        try:
            micros = parse_amount_to_micros(args[0])
            result = convert(micros, args[1], args[2])
            self._send(chat_id, format_fx_html(result, lang=lang))
        except FxError as exc:
            self._send(
                chat_id,
                t("fx_fail", lang, err=escape_html(redact_exc(exc, max_len=256))),
            )
        except Exception as exc:  # noqa: BLE001
            self._send(
                chat_id,
                t("fx_fail", lang, err=escape_html(redact_exc(exc, max_len=256))),
            )

    def _cmd_bubbles(self, chat_id: int) -> None:
        """Top gainers / losers ~6m from live bubble book."""
        from radar.ops.bubbles import get_bubbles

        lang = self._lang(chat_id)
        cfg = (load_config().get("bubbles") or {})
        data = get_bubbles(
            top_n=int(cfg.get("top_n") or 100),
            cache_ttl_ms=int(cfg.get("cache_ttl_ms") or 30_000),
        )
        coins = [
            c
            for c in (data.get("coins") or [])
            if isinstance(c, dict) and c.get("change_6m_bps") is not None
        ]
        title = "Bubbles · ~6m" if lang == "en" else "Bubbles · ~6м"
        if not coins:
            err = escape_html(str(data.get("error") or "empty")[:256])
            self._send(chat_id, f"<b>{title}</b>\n{err}")
            return
        gain = sorted(coins, key=lambda c: int(c["change_6m_bps"]), reverse=True)[:5]
        lose = sorted(coins, key=lambda c: int(c["change_6m_bps"]))[:5]

        def _line(c: Dict[str, Any]) -> str:
            bps = int(c["change_6m_bps"])
            sign = "+" if bps > 0 else ("-" if bps < 0 else "")
            abs_bps = abs(bps)
            pct = f"{sign}{abs_bps // 100}.{abs_bps % 100:02d}%"
            return (
                f"<code>{escape_html(str(c.get('symbol')))}</code> "
                f"{escape_html(pct)}"
            )

        up = "Gainers" if lang == "en" else "Рост"
        down = "Losers" if lang == "en" else "Падение"
        lines = [
            f"<b>{title}</b> · n={len(coins)}",
            f"<b>{up}</b>",
            *[_line(c) for c in gain],
            f"<b>{down}</b>",
            *[_line(c) for c in lose],
            f"<i>{escape_html(str(data.get('period_label') or ''))}</i>",
        ]
        self._send(chat_id, "\n".join(lines))

    def _cmd_status(self, chat_id: int) -> None:
        snap = self._snapshot_fn() or {}
        auto = self._auto_status_fn() if self._auto_status_fn else {}
        lines = [
            "<b>Radar status</b>",
            f"scan_ok={snap.get('ok')} · backend=<code>{snap.get('score_backend')}</code>",
            f"universe={snap.get('universe_size')} · elapsed_ms={snap.get('elapsed_ms')}",
            f"auto_enabled={auto.get('enabled')} · positions={auto.get('open_positions')} "
            f"· persist={auto.get('persistence_ok')}",
            f"tg_watches={self._book.status().get('count')} · persist={self._book.persist_ok}",
        ]
        self._send(chat_id, "\n".join(lines))

    def _cmd_top(self, chat_id: int, args: List[str]) -> None:
        sector = (args[0] if args else "spot_alts").lower()
        snap = self._snapshot_fn() or {}
        rows = (snap.get("sectors") or {}).get(sector) or []
        if not rows:
            self._send(
                chat_id,
                f"сектор <code>{escape_html(sector)}</code> пуст или неизвестен",
            )
            return
        lines = [f"<b>Top · {escape_html(sector)}</b>"]
        for i, r in enumerate(rows[:10], 1):
            lines.append(
                f"{i}. <code>{escape_html(r.get('symbol'))}</code> "
                f"{display_price(r.get('price_i'))} · "
                f"{escape_html(r.get('setup'))} · "
                f"score={escape_html(r.get('score_centi'))}"
            )
        self._send(chat_id, "\n".join(lines))

    def _cmd_watch(self, chat_id: int, args: List[str]) -> None:
        if not args:
            self._send(chat_id, "usage: /watch BTCUSDT [spot|long|short] [entry_price]")
            return
        symbol = args[0]
        market = "spot"
        entry = None
        if len(args) >= 2:
            m = args[1].lower()
            if m in {"spot"}:
                market = "spot"
            elif m in {"long", "futures_long", "fut_long"}:
                market = "futures_long"
            elif m in {"short", "futures_short", "fut_short"}:
                market = "futures_short"
            else:
                entry = price_i(args[1])
        if len(args) >= 3:
            entry = price_i(args[2])
        # default entry from snapshot mark
        if entry is None:
            row = find_row_public(self._snapshot_fn() or {}, symbol)
            if row and row.get("price_i"):
                entry = int(row["price_i"])
        try:
            row = self._book.add(chat_id, symbol, market=market, entry_price_i=entry)
        except (ValueError, WatchBookFull) as exc:
            self._send(chat_id, redact_exc(exc, max_len=256))
            return
        near_action = {
            "spot": "sell",
            "futures_long": "close_long",
            "futures_short": "close_short",
        }[market]
        snap_row = find_row_public(self._snapshot_fn() or {}, symbol)
        near = {}
        if snap_row and snap_row.get("price_i"):
            near = nearest_for_action(
                action=near_action,
                price_i=int(snap_row["price_i"]),
                setup=str(snap_row.get("setup") or ""),
                entry_price_i=entry,
            )
        msg = (
            f"✅ watch <code>{escape_html(row['symbol'])}</code> · "
            f"{escape_html(row['market'])}\n"
            f"entry={display_price(row.get('entry_price_i'))}\n"
            f"буду слать WARN когда анализ скажет слить/закрыть"
        )
        if near.get("ok"):
            msg += (
                f"\nближайший выход: <b>{escape_html(near['nearest_display'])}</b> "
                f"({escape_html(near['hint'])})"
            )
        self._send(chat_id, msg)

    def _cmd_unwatch(self, chat_id: int, args: List[str]) -> None:
        if not args:
            self._send(chat_id, "usage: /unwatch SYMBOL")
            return
        n = self._book.remove(chat_id, args[0])
        self._send(chat_id, f"снято слежений: {n}")

    def _cmd_positions(self, chat_id: int) -> None:
        rows = self._book.list_for_chat(chat_id)
        if not rows:
            self._send(chat_id, "нет слежений — /watch SYMBOL")
            return
        lines = ["<b>Ваши watches</b>"]
        for w in rows:
            lines.append(
                f"· <code>{escape_html(w.get('symbol'))}</code> "
                f"{escape_html(w.get('market'))} "
                f"entry={display_price(w.get('entry_price_i'))}"
            )
        self._send(chat_id, "\n".join(lines))

    def _cmd_nearest(self, chat_id: int, args: List[str]) -> None:
        if not args:
            self._send(
                chat_id,
                "usage: /nearest SYMBOL [buy|sell|long|short|close_long|close_short]",
            )
            return
        symbol = args[0]
        action = (args[1] if len(args) > 1 else "buy").lower()
        row = find_row_public(self._snapshot_fn() or {}, symbol)
        if not row or not row.get("price_i"):
            self._send(
                chat_id,
                f"нет строки анализа для <code>{escape_html(norm_symbol(symbol))}</code>",
            )
            return
        n = nearest_for_action(
            action=action,
            price_i=int(row["price_i"]),
            setup=str(row.get("setup") or ""),
        )
        if not n.get("ok"):
            self._send(chat_id, escape_html(n.get("error") or "fail"))
            return
        self._send(
            chat_id,
            "\n".join(
                [
                    f"<b>Nearest · {escape_html(norm_symbol(symbol))}</b> · "
                    f"{escape_html(action)}",
                    f"setup=<code>{escape_html(row.get('setup'))}</code> "
                    f"mark={escape_html(n['mark_display'])}",
                    f"зона {escape_html(n['zone_lo_display'])}–"
                    f"{escape_html(n['zone_hi_display'])}",
                    f"→ <b>{escape_html(n['nearest_display'])}</b>",
                    escape_html(n["hint"]),
                    f"<i>{escape_html(n['disclaimer'])}</i>",
                ]
            ),
        )

    def _cmd_quote(self, chat_id: int, args: List[str]) -> None:
        if not args:
            self._send(chat_id, "usage: /quote SYMBOL")
            return
        row = find_row_public(self._snapshot_fn() or {}, args[0])
        if not row:
            self._send(chat_id, "нет в текущем snapshot")
            return
        self._send(
            chat_id,
            f"<code>{escape_html(row.get('symbol'))}</code> "
            f"{display_price(row.get('price_i'))}\n"
            f"setup={escape_html(row.get('setup'))} "
            f"score={escape_html(row.get('score_centi'))}\n"
            f"{escape_html(row.get('entry_hint') or '')}",
        )

    def _auto_watch_after_buy(
        self,
        chat_id: int,
        symbol: str,
        entry_price_i: Optional[int],
    ) -> str:
        """Add spot watch after live buy; never silent on failure."""
        try:
            self._book.add(
                chat_id,
                symbol,
                market="spot",
                entry_price_i=entry_price_i,
                note="tg-buy",
            )
            return "\nwatch: spot added"
        except Exception as exc:  # noqa: BLE001
            safe = redact_exc(exc, max_len=256)
            logger.error(
                "auto-watch after buy failed chat=%s symbol=%s err=%s",
                chat_id,
                symbol,
                safe,
            )
            TG_STATS.inc("watch_add_fail")
            return f"\n⚠ watch NOT added: {safe}"

    def _cmd_trade(self, chat_id: int, side: str, args: List[str]) -> None:
        """Manual spot only. Futures refused. Two-step: without confirm=preview."""
        if not self._trade_place_fn:
            self._send(chat_id, "trade hook не подключён")
            return
        if len(args) < 2:
            self._send(
                chat_id,
                f"usage: /{side} SYMBOL AMOUNT [confirm]\n"
                f"buy AMOUNT=USDT · sell AMOUNT=base · только spot",
            )
            return
        try:
            symbol = normalize_trade_symbol(args[0])
        except SymbolValidationError as exc:
            self._send(chat_id, f"symbol refused: {escape_html(exc)}")
            return
        amount = args[1]
        if len(amount) > 32:
            self._send(chat_id, "amount too long")
            return
        confirm = len(args) >= 3 and args[2].lower() in {"confirm", "yes", "да"}
        # Suggest nearest before place
        row = find_row_public(self._snapshot_fn() or {}, symbol)
        near_txt = ""
        if row and row.get("price_i"):
            n = nearest_for_action(
                action=side,
                price_i=int(row["price_i"]),
                setup=str(row.get("setup") or ""),
            )
            if n.get("ok"):
                near_txt = (
                    f"\nанализ nearest={escape_html(n['nearest_display'])} "
                    f"({escape_html(n['hint'])})"
                )
        try:
            if side == "buy":
                micros = quote_i(amount)
                if not micros or micros <= 0:
                    self._send(chat_id, "сумма USDT невалидна")
                    return
                result = self._trade_place_fn(
                    symbol=symbol,
                    side="buy",
                    confirm=confirm,
                    quote_usdt_micros=int(micros),
                )
            else:
                from radar.money import base_i as to_base

                size = to_base(amount)
                if not size or size <= 0:
                    self._send(chat_id, "size base невалиден")
                    return
                result = self._trade_place_fn(
                    symbol=symbol,
                    side="sell",
                    confirm=confirm,
                    base_size_i=int(size),
                )
        except Exception as exc:  # noqa: BLE001
            self._send(chat_id, f"trade error: {redact_exc(exc, max_len=256)}")
            return
        ok = getattr(result, "ok", None)
        if ok is None and isinstance(result, dict):
            live_ok = bool(
                confirm and result.get("ok") and not result.get("dry_run")
            )
            msg = (
                f"{'✅' if result.get('ok') else '❌'} {escape_html(side)} "
                f"<code>{escape_html(norm_symbol(symbol))}</code>\n"
                f"{escape_html(result.get('message'))}"
                f"{near_txt}"
            )
            if result.get("fill"):
                fill = result["fill"]
                msg += (
                    f"\nfill base_size_i={fill.get('base_size_i')} "
                    f"basis={fill.get('size_basis')}"
                )
            if live_ok and side == "buy":
                entry = (
                    int(row["price_i"]) if row and row.get("price_i") else None
                )
                msg += self._auto_watch_after_buy(chat_id, symbol, entry)
            self._send(chat_id, msg)
            return
        # TradeResult dataclass
        live_ok = bool(confirm and result.ok and not result.dry_run)
        msg = (
            f"{'✅' if result.ok else '❌'} {escape_html(side)} "
            f"<code>{escape_html(norm_symbol(symbol))}</code>\n"
            f"{escape_html(result.message)} · dry={escape_html(result.dry_run)}"
            f"{near_txt}"
        )
        if live_ok and side == "buy":
            entry = int(row["price_i"]) if row and row.get("price_i") else None
            msg += self._auto_watch_after_buy(chat_id, symbol, entry)
        self._send(chat_id, msg)

"""Evaluate sell/close warnings for watched positions against snapshot."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from radar.html_escape import escape_html
from radar.money import display_bps, display_price, display_score_centi, drop_bps
from radar.telegram.nearest import nearest_for_action
from radar.telegram.watches import WatchBook, norm_symbol


def _append_mtf_reasons(row: Dict[str, Any], reasons: List[str]) -> None:
    """Surface short-window MTF from metrics (honesty with dashboard)."""
    m = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    bps_15 = m.get("change_bps_15m")
    bps_1h = m.get("change_bps_1h")
    if bps_15 is not None and abs(int(bps_15)) >= 30:
        reasons.append(f"mtf_15m={display_bps(int(bps_15))}")
    if bps_1h is not None and abs(int(bps_1h)) >= 50:
        reasons.append(f"mtf_1h={display_bps(int(bps_1h))}")
    age = m.get("age_hours")
    if age is not None and int(age) <= 24:
        reasons.append(f"listing_age_h={int(age)}")
    live_v = m.get("live_venue")
    row_v = row.get("venue")
    if live_v and row_v and str(live_v) != str(row_v):
        reasons.append(f"live_venue={str(live_v)[:32]}")


def _find_row(snap: Dict[str, Any], symbol: str) -> Optional[Dict[str, Any]]:
    sym = norm_symbol(symbol)
    sectors = snap.get("sectors") or {}
    # Prefer exact sector order: spot then futures then rest
    order = [
        "spot_alts",
        "memes",
        "listings",
        "futures_long",
        "futures_short",
        "futures",
        "whales",
        "stables",
    ]
    seen = set(order)
    keys = order + [k for k in sectors.keys() if k not in seen]
    for sector in keys:
        for row in sectors.get(sector) or []:
            if norm_symbol(str(row.get("symbol") or "")) == sym:
                out = dict(row)
                out["_sector"] = sector
                return out
    return None


def evaluate_watch(
    watch: Dict[str, Any],
    snap: Dict[str, Any],
    *,
    stop_loss_bps: int = 1200,
    take_profit_warn_bps: int = 800,
    alert_cooldown_ms: int = 300_000,
    mark_price_fn: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    """Return alert dict or None.

    If symbol is outside top-N snapshot but ``mark_price_fn`` is set and the
    watch has ``entry_price_i``, still evaluate **spot stop-loss only** (no
    fade/setup spam without a scored row).
    """
    market = str(watch.get("market") or "spot")
    symbol = str(watch.get("symbol") or "")
    row = _find_row(snap, symbol)
    entry_i = watch.get("entry_price_i")
    entry_i = int(entry_i) if entry_i else None

    if not row:
        if (
            market == "spot"
            and entry_i
            and entry_i > 0
            and callable(mark_price_fn)
        ):
            try:
                mark = mark_price_fn(symbol)
            except Exception:  # noqa: BLE001
                return None
            if not mark or int(mark) <= 0:
                return None
            price_i = int(mark)
            drop = drop_bps(entry_i, price_i)
            if drop < stop_loss_bps:
                return None
            now_ms = int(time.time() * 1000)
            last_ms = int(watch.get("last_alert_at_ms") or 0)
            last_key = str(watch.get("last_alert_key") or "")
            reasons = [
                f"stop-loss drop={display_bps(drop)} ≥ {display_bps(stop_loss_bps)}",
                "outside_top_mark — symbol not in current top-N; mark from exchange",
            ]
            alert_key = f"critical|sell|outside_top|{reasons[0][:40]}"
            if alert_key == last_key and (now_ms - last_ms) < alert_cooldown_ms:
                return None
            nearest = nearest_for_action(
                action="sell",
                price_i=price_i,
                setup="fade-watch",
                entry_price_i=entry_i,
            )
            return {
                "symbol": symbol,
                "market": market,
                "severity": "critical",
                "action_suggest": "sell",
                "reasons": reasons,
                "setup": "outside_top",
                "price_i": price_i,
                "price_display": display_price(price_i),
                "score_display": "—",
                "sector": None,
                "nearest": nearest,
                "alert_key": alert_key,
                "chat_id": watch.get("chat_id"),
            }
        return None

    price_i = int(row.get("price_i") or 0)
    setup = str(row.get("setup") or "")
    score = row.get("score_centi")
    now_ms = int(time.time() * 1000)
    last_ms = int(watch.get("last_alert_at_ms") or 0)
    last_key = str(watch.get("last_alert_key") or "")

    reasons: List[str] = []
    severity = "info"
    action_suggest = "hold"
    nearest_action = "sell"

    if market == "spot":
        nearest_action = "sell"
        if setup in {"fade-watch", "short"}:
            reasons.append(f"setup={setup} — анализ говорит смотреть слив/выход")
            severity = "warn"
            action_suggest = "sell"
        if entry_i and price_i > 0 and entry_i > 0:
            drop = drop_bps(entry_i, price_i)
            if drop >= stop_loss_bps:
                reasons.append(f"stop-loss drop={display_bps(drop)} ≥ {display_bps(stop_loss_bps)}")
                severity = "critical"
                action_suggest = "sell"
            gain = -drop  # price up from entry → negative drop
            if gain >= take_profit_warn_bps and setup == "fade-watch":
                reasons.append(
                    f"в плюсе ~{display_bps(gain)} и fade-watch — зафиксировать прибыль?"
                )
                severity = "warn"
                action_suggest = "sell"
        if row.get("risk_flags"):
            flags = [str(f)[:64] for f in (row.get("risk_flags") or [])[:8]]
            reasons.append("risk_flags=" + ",".join(flags))
            if severity == "info":
                severity = "warn"
        _append_mtf_reasons(row, reasons)

    elif market == "futures_long":
        nearest_action = "close_long"
        if setup == "short":
            reasons.append("futures setup=short — закрыть LONG")
            severity = "critical"
            action_suggest = "close_long"
        if setup == "fade-watch":
            reasons.append("fade на споте при LONG — снизить риск / закрыть")
            severity = "warn"
            action_suggest = "close_long"
        if entry_i and price_i > 0:
            drop = drop_bps(entry_i, price_i)
            if drop >= stop_loss_bps:
                reasons.append(f"LONG stop-loss drop={display_bps(drop)}")
                severity = "critical"
                action_suggest = "close_long"
        _append_mtf_reasons(row, reasons)

    elif market == "futures_short":
        nearest_action = "close_short"
        if setup == "long":
            reasons.append("futures setup=long — закрыть SHORT")
            severity = "critical"
            action_suggest = "close_short"
        if entry_i and price_i > 0:
            # short loses when price rises
            rise = -drop_bps(entry_i, price_i)
            if rise >= stop_loss_bps:
                reasons.append(f"SHORT против нас rise={display_bps(rise)}")
                severity = "critical"
                action_suggest = "close_short"
        _append_mtf_reasons(row, reasons)

    else:
        # listings / whales / unknown market — still surface MTF + listing flags
        _append_mtf_reasons(row, reasons)
        flags = [str(f)[:64] for f in (row.get("risk_flags") or [])[:8]]
        if "cross_venue_live" in flags:
            reasons.append("cross_venue_live — тикер с другой биржи")
            if severity == "info":
                severity = "warn"
        if "no_live_volume" in flags:
            reasons.append("no_live_volume — тонкий стакан")
            if severity == "info":
                severity = "warn"
        if setup == "new-listing-watch" and not reasons:
            # Fresh listing alone is not an exit alert; skip spam.
            return None

    if not reasons:
        return None

    alert_key = f"{severity}|{action_suggest}|{setup}|{reasons[0][:40]}"
    if alert_key == last_key and (now_ms - last_ms) < alert_cooldown_ms:
        return None

    nearest = nearest_for_action(
        action=nearest_action,
        price_i=price_i,
        setup=setup,
        entry_price_i=entry_i,
    )

    return {
        "symbol": symbol,
        "market": market,
        "severity": severity,
        "action_suggest": action_suggest,
        "reasons": reasons,
        "setup": setup,
        "price_i": price_i,
        "price_display": display_price(price_i),
        "score_display": display_score_centi(score),
        "sector": row.get("_sector"),
        "nearest": nearest,
        "alert_key": alert_key,
        "chat_id": watch.get("chat_id"),
    }


def scan_all(
    book: WatchBook,
    snap: Dict[str, Any],
    *,
    stop_loss_bps: int = 1200,
    take_profit_warn_bps: int = 800,
    alert_cooldown_ms: int = 300_000,
    mark_price_fn: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for w in book.all():
        alert = evaluate_watch(
            w,
            snap,
            stop_loss_bps=stop_loss_bps,
            take_profit_warn_bps=take_profit_warn_bps,
            alert_cooldown_ms=alert_cooldown_ms,
            mark_price_fn=mark_price_fn,
        )
        if alert:
            out.append(alert)
    return out


def format_alert(alert: Dict[str, Any]) -> str:
    sev_raw = str(alert.get("severity") or "info").upper()
    icon = {"CRITICAL": "🚨", "WARN": "⚠️", "INFO": "ℹ️"}.get(sev_raw, "⚠️")
    sev = escape_html(sev_raw)
    sym = escape_html(alert.get("symbol"))
    market = escape_html(alert.get("market"))
    setup = escape_html(alert.get("setup"))
    lines = [
        f"{icon} <b>Radar alert · {sev}</b>",
        f"<b>{sym}</b> · {market} · setup=<code>{setup}</code>",
        f"mark={escape_html(alert.get('price_display'))} · "
        f"score={escape_html(alert.get('score_display'))}",
        f"→ рекоменд.: <b>{escape_html(alert.get('action_suggest'))}</b>",
    ]
    for r in list(alert.get("reasons") or [])[:16]:
        lines.append(f"· {escape_html(str(r)[:256])}")
    n = alert.get("nearest") or {}
    if n.get("ok"):
        lines.append(
            f"ближайшая точка ({escape_html(n.get('kind'))}): "
            f"<b>{escape_html(n.get('nearest_display'))}</b> "
            f"[{escape_html(n.get('zone_lo_display'))}–"
            f"{escape_html(n.get('zone_hi_display'))}]"
        )
        lines.append(f"<i>{escape_html(n.get('disclaimer'))}</i>")
    lines.append("<i>Не финсовет. Futures автоматом не торгуются.</i>")
    return "\n".join(lines)


def find_row_public(snap: Dict[str, Any], symbol: str) -> Optional[Dict[str, Any]]:
    return _find_row(snap, symbol)

"""Snapshot digests for Telegram — 1h movers + extreme funding. Integer money."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from radar.html_escape import escape_html
from radar.money import PRICE_SCALE, display_bps, display_price, mul_div

DIGEST_SECTORS = ("spot_alts", "memes", "listings")


def _row_bps_1h(row: Dict[str, Any]) -> Optional[int]:
    m = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    v = m.get("change_bps_1h")
    if v is None:
        v = row.get("change_bps_1h")
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _row_funding_i(row: Dict[str, Any]) -> Optional[int]:
    m = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    v = m.get("funding_rate_i")
    if v is None:
        v = row.get("funding_rate_i")
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def funding_display_bps(funding_rate_i: int) -> int:
    """Map 1e8-fraction funding to display bps (100 bps = 1%)."""
    return int(mul_div(int(funding_rate_i), 10_000, PRICE_SCALE))


def collect_1h_movers(
    snap: Dict[str, Any],
    *,
    sectors: Tuple[str, ...] = DIGEST_SECTORS,
    top_n: int = 5,
) -> Dict[str, Any]:
    """Top gainers/losers by change_bps_1h across digest sectors."""
    seen: Dict[str, Dict[str, Any]] = {}
    for sector in sectors:
        rows = (snap.get("sectors") or {}).get(sector) or []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            sym = str(row.get("symbol") or "").upper()
            bps = _row_bps_1h(row)
            if not sym or bps is None:
                continue
            prev = seen.get(sym)
            if prev is None or abs(bps) > abs(int(prev["change_bps_1h"])):
                seen[sym] = {
                    "symbol": sym,
                    "change_bps_1h": int(bps),
                    "price_i": row.get("price_i"),
                    "sector": sector,
                    "setup": row.get("setup"),
                }
    items = list(seen.values())
    gain = sorted(items, key=lambda r: int(r["change_bps_1h"]), reverse=True)[:top_n]
    lose = sorted(items, key=lambda r: int(r["change_bps_1h"]))[:top_n]
    return {
        "n_with_1h": len(items),
        "gainers": gain,
        "losers": lose,
    }


def format_1h_digest_html(data: Dict[str, Any], *, lang: str = "ru") -> str:
    title = "1h digest" if lang == "en" else "Дайджест 1ч"
    up = "Gainers" if lang == "en" else "Рост"
    down = "Losers" if lang == "en" else "Падение"
    empty = "no 1h metrics" if lang == "en" else "нет метрик 1ч"

    def _line(r: Dict[str, Any]) -> str:
        return (
            f"<code>{escape_html(r.get('symbol'))}</code> "
            f"{escape_html(display_bps(r.get('change_bps_1h')))} · "
            f"{escape_html(display_price(r.get('price_i')))}"
        )

    gain = data.get("gainers") or []
    lose = data.get("losers") or []
    if not gain and not lose:
        return f"<b>{title}</b>\n{empty}"
    lines = [
        f"<b>{title}</b> · n={int(data.get('n_with_1h') or 0)}",
        f"<b>{up}</b>",
        *[_line(r) for r in gain],
        f"<b>{down}</b>",
        *[_line(r) for r in lose],
    ]
    return "\n".join(lines)


def collect_extreme_funding(
    snap: Dict[str, Any],
    *,
    top_n: int = 8,
) -> List[Dict[str, Any]]:
    """Futures rows sorted by |funding_rate_i|."""
    rows = (snap.get("sectors") or {}).get("futures") or []
    if not isinstance(rows, list):
        # fallback: merge long/short boards
        rows = []
        for key in ("futures_long", "futures_short", "futures"):
            part = (snap.get("sectors") or {}).get(key) or []
            if isinstance(part, list):
                rows.extend(part)
    out: List[Dict[str, Any]] = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        sym = str(row.get("symbol") or "").upper()
        fr = _row_funding_i(row)
        if not sym or fr is None or sym in seen:
            continue
        seen.add(sym)
        out.append(
            {
                "symbol": sym,
                "funding_rate_i": int(fr),
                "funding_display_bps": funding_display_bps(int(fr)),
                "price_i": row.get("price_i"),
                "setup": row.get("setup"),
            }
        )
    out.sort(key=lambda r: abs(int(r["funding_rate_i"])), reverse=True)
    return out[: max(1, int(top_n))]


def format_funding_html(rows: List[Dict[str, Any]], *, lang: str = "ru") -> str:
    title = "Funding extremes" if lang == "en" else "Экстремальный funding"
    empty = "no funding rows" if lang == "en" else "нет funding в снимке"
    if not rows:
        return f"<b>{title}</b>\n{empty}"
    lines = [f"<b>{title}</b>"]
    for r in rows:
        lines.append(
            f"<code>{escape_html(r.get('symbol'))}</code> "
            f"{escape_html(display_bps(r.get('funding_display_bps')))} · "
            f"{escape_html(display_price(r.get('price_i')))}"
        )
    note = (
        "rate as % of notional / period · not advice"
        if lang == "en"
        else "ставка % нотионала / период · не совет"
    )
    lines.append(f"<i>{note}</i>")
    return "\n".join(lines)

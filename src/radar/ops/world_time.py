"""World clock for ops — fixed UTC offsets (no tzdata required on Windows)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple

# (id, label_en, label_ru, utc_offset_minutes) — standard time; DST not applied.
_CITIES: Tuple[Tuple[str, str, str, int], ...] = (
    ("utc", "UTC", "UTC", 0),
    ("london", "London", "Лондон", 0),
    ("berlin", "Berlin", "Берлин", 60),
    ("moscow", "Moscow", "Москва", 180),
    ("dubai", "Dubai", "Дубай", 240),
    ("singapore", "Singapore", "Сингапур", 480),
    ("tokyo", "Tokyo", "Токио", 540),
    ("sydney", "Sydney", "Сидней", 600),
    ("new_york", "New York", "Нью-Йорк", -300),
    ("chicago", "Chicago", "Чикаго", -360),
    ("los_angeles", "Los Angeles", "Лос-Анджелес", -480),
)


def world_clock(*, lang: str = "en", now: datetime | None = None) -> Dict[str, Any]:
    """Return integer epoch + per-city HH:MM:SS strings."""
    base = now or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    else:
        base = base.astimezone(timezone.utc)
    epoch_ms = int(base.timestamp() * 1000)
    rows: List[Dict[str, Any]] = []
    use_ru = (lang or "").lower().startswith("ru")
    for cid, en, ru, off_min in _CITIES:
        local = base + timedelta(minutes=int(off_min))
        rows.append(
            {
                "id": cid,
                "label": ru if use_ru else en,
                "utc_offset_min": int(off_min),
                "hhmmss": local.strftime("%H:%M:%S"),
                "ymd": local.strftime("%Y-%m-%d"),
            }
        )
    return {
        "ok": True,
        "utc_epoch_ms": epoch_ms,
        "utc_iso": base.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note": "fixed UTC offsets (no DST)",
        "cities": rows,
    }


def format_world_clock_html(*, lang: str = "en") -> str:
    from radar.i18n import t
    from radar.i18n import normalize_lang

    lg = normalize_lang(lang)
    data = world_clock(lang=lg)
    lines = [t("time_title", lg), f"<code>UTC {data['utc_iso']}</code>"]
    for c in data["cities"]:
        off_min = int(c["utc_offset_min"])
        sign = "+" if off_min >= 0 else "-"
        abs_m = abs(off_min)
        if abs_m % 60 == 0:
            off = f"UTC{sign}{abs_m // 60}"
        else:
            off = f"UTC{sign}{abs_m // 60}:{abs_m % 60:02d}"
        lines.append(
            f"{c['label']}: <code>{c['ymd']} {c['hhmmss']}</code> · {off}"
        )
    lines.append(f"<i>{data['note']}</i>")
    return "\n".join(lines)

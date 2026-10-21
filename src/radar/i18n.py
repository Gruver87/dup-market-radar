"""EN/RU UI strings for Telegram bot + shared ops labels."""

from __future__ import annotations

from typing import Dict, Literal

Lang = Literal["en", "ru"]
DEFAULT_LANG: Lang = "ru"
VALID_LANGS = frozenset({"en", "ru"})


def normalize_lang(raw: str | None) -> Lang:
    s = (raw or "").strip().lower()
    if len(s) > 32:
        return DEFAULT_LANG
    if s in {"en", "eng", "english"}:
        return "en"
    if s in {"ru", "rus", "russian", "рус", "русский"}:
        return "ru"
    return DEFAULT_LANG


_STRINGS: Dict[str, Dict[Lang, str]] = {
    "help": {
        "ru": (
            "<b>DUP Market Radar · Telegram</b>\n\n"
            "<b>Watch / алерты</b>\n"
            "/watch SYMBOL [spot|long|short] [entry]\n"
            "  — следить; long/short = futures watch (без авто-ордеров)\n"
            "/unwatch SYMBOL\n"
            "/positions — ваши слежения\n"
            "/alerts — проверить сейчас\n\n"
            "<b>Анализ</b>\n"
            "/top [sector] — топ сетапов\n"
            "/nearest SYMBOL [buy|sell|long|short|close_long|close_short]\n"
            "  — ближайшая точка входа/выхода по анализу\n"
            "/quote SYMBOL — текущий mark + setup\n\n"
            "<b>Ops</b>\n"
            "/time — мировое время (UTC + города)\n"
            "/fx AMOUNT FROM TO — курс (ECB/frankfurter), напр. /fx 100 USD EUR\n"
            "/bubbles — топ рост/падение ~6м (crypto bubbles)\n"
            "/lang en|ru — язык ответов\n\n"
            "<b>Статус</b>\n"
            "/status — radar / auto / watches\n"
            "/help\n\n"
            "<i>Алерты: fade / stop-loss / разворот futures → предупреждение.\n"
            "Nearest = зона анализа, не стакан. Не финсовет.</i>"
        ),
        "en": (
            "<b>DUP Market Radar · Telegram</b>\n\n"
            "<b>Watch / alerts</b>\n"
            "/watch SYMBOL [spot|long|short] [entry]\n"
            "  — track; long/short = futures watch (no auto orders)\n"
            "/unwatch SYMBOL\n"
            "/positions — your watches\n"
            "/alerts — check now\n\n"
            "<b>Analysis</b>\n"
            "/top [sector] — top setups\n"
            "/nearest SYMBOL [buy|sell|long|short|close_long|close_short]\n"
            "  — nearest entry/exit zone from analysis\n"
            "/quote SYMBOL — mark + setup\n\n"
            "<b>Ops</b>\n"
            "/time — world clock (UTC + cities)\n"
            "/fx AMOUNT FROM TO — FX (ECB/frankfurter), e.g. /fx 100 USD EUR\n"
            "/bubbles — top gainers/losers ~6m (crypto bubbles)\n"
            "/lang en|ru — reply language\n\n"
            "<b>Status</b>\n"
            "/status — radar / auto / watches\n"
            "/help\n\n"
            "<i>Alerts: fade / stop-loss / futures flip → warning.\n"
            "Nearest = analysis zone, not the order book. Not financial advice.</i>"
        ),
    },
    "lang_set": {
        "ru": "язык: <b>русский</b>",
        "en": "language: <b>English</b>",
    },
    "lang_usage": {
        "ru": "usage: /lang en|ru",
        "en": "usage: /lang en|ru",
    },
    "unknown_cmd": {
        "ru": "неизвестная команда — /help",
        "en": "unknown command — /help",
    },
    "cmd_rate": {
        "ru": "слишком много команд — soft refuse, retry ~{sec}s",
        "en": "too many commands — soft refuse, retry ~{sec}s",
    },
    "alerts_done": {
        "ru": "проверка алертов выполнена (если были — уже ушли)",
        "en": "alert check done (any matches already sent)",
    },
    "fx_usage": {
        "ru": "usage: /fx 100 USD EUR",
        "en": "usage: /fx 100 USD EUR",
    },
    "fx_fail": {
        "ru": "курс недоступен: {err}",
        "en": "FX unavailable: {err}",
    },
    "time_title": {
        "ru": "<b>Мировое время</b>",
        "en": "<b>World clock</b>",
    },
    "not_fin_advice": {
        "ru": "Не финансовый совет.",
        "en": "Not financial advice.",
    },
}


def t(key: str, lang: Lang, **kwargs: object) -> str:
    row = _STRINGS.get(key) or {}
    raw = row.get(lang) or row.get(DEFAULT_LANG) or key
    if kwargs:
        # Bound format args — refuse huge injection into Telegram HTML templates
        safe = {
            k: (str(v)[:256] if v is not None else "")
            for k, v in list(kwargs.items())[:32]
        }
        try:
            return raw.format(**safe)
        except (KeyError, ValueError):
            return raw
    return raw

"""Telegram secrets from env — never hardcode tokens."""

from __future__ import annotations

import logging
import os
from typing import List, Optional

from radar.secrets_redact import register_secret
from radar.trade.env_load import load_dotenv

logger = logging.getLogger("radar.telegram.env")


# Bot tokens are ~40–60 chars; soft-refuse absurd dumps / truncations.
_TOKEN_MIN_LEN = 20
_TOKEN_MAX_LEN = 256


def telegram_bot_token() -> Optional[str]:
    load_dotenv()
    tok = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not tok:
        return None
    if len(tok) < _TOKEN_MIN_LEN or len(tok) > _TOKEN_MAX_LEN:
        logger.error(
            "TELEGRAM_BOT_TOKEN length refused len=%s (need %s..%s)",
            len(tok),
            _TOKEN_MIN_LEN,
            _TOKEN_MAX_LEN,
        )
        return None
    register_secret(tok)
    return tok


# Soft cap allow-listed chats (ops / DoS via huge env lists).
_MAX_ALLOWED_CHATS = 64


def telegram_allowed_chat_ids() -> List[int]:
    load_dotenv()
    raw = (os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS") or "").strip()
    if not raw:
        # single-chat convenience
        one = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
        raw = one
    out: List[int] = []
    for part in raw.replace(";", ",").split(","):
        if len(out) >= _MAX_ALLOWED_CHATS:
            logger.warning(
                "TELEGRAM_ALLOWED_CHAT_IDS truncated at max=%s", _MAX_ALLOWED_CHATS
            )
            break
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            logger.warning("TELEGRAM chat id refused (not int): %r", part[:32])
            continue
    return out


def telegram_enabled_env() -> bool:
    load_dotenv()
    flag = (os.environ.get("TELEGRAM_ENABLED") or "").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    if flag in {"1", "true", "yes", "on"}:
        return True
    # default: enabled when token + at least one chat present
    return bool(telegram_bot_token() and telegram_allowed_chat_ids())

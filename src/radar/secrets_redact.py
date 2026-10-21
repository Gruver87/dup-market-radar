"""Fail-closed secret redaction for logs, audit JSONL, and exception text.

Never let Telegram bot tokens, KuCoin API credentials, or RADAR_API_KEY
appear in process logs or rotating audit files.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from typing import Any, Iterable, Mapping, Optional

# Env vars treated as secrets when present (value length >= MIN_SECRET_LEN).
SECRET_ENV_KEYS: tuple[str, ...] = (
    "TELEGRAM_BOT_TOKEN",
    "KUCOIN_API_KEY",
    "KUCOIN_API_SECRET",
    "KUCOIN_API_PASSPHRASE",
    "RADAR_API_KEY",
)

MIN_SECRET_LEN = 8
REDACTED = "***REDACTED***"
# Soft caps — huge log lines / unbounded register_secret sets
MAX_REDACT_CHARS = 65_536
MAX_REGISTERED_SECRETS = 64

# Telegram BotFather tokens: <bot_id>:<secret>
_RE_TG_TOKEN = re.compile(r"\b\d{5,15}:[A-Za-z0-9_-]{20,}\b")
# Full Bot API URL path segment
_RE_TG_BOT_URL = re.compile(
    r"(https?://api\.telegram\.org/bot)([^\s/\"'\\]+)",
    re.IGNORECASE,
)
# Common auth header / JSON key dumps
_RE_AUTH_HEADER = re.compile(
    r"(?i)\b("
    r"KC-API-KEY|KC-API-SIGN|KC-API-PASSPHRASE|KC-API-TIMESTAMP|"
    r"X-Radar-Api-Key|Authorization|Bearer|"
    r"api[_-]?key|api[_-]?secret|api[_-]?passphrase|bot[_-]?token|password|secret"
    r")\b(\s*[:=]\s*)([^\s,;\"']+|\"[^\"]*\"|'[^']*')"
)
_RE_JSON_SECRET = re.compile(
    r'(?i)("(?:api_key|api_secret|api_passphrase|token|bot_token|password|secret|'
    r'KC-API-KEY|KC-API-SIGN|KC-API-PASSPHRASE|TELEGRAM_BOT_TOKEN|RADAR_API_KEY)"\s*:\s*")'
    r'([^"]*)(")'
)

_extra_lock = threading.Lock()
_extra_secrets: set[str] = set()
_configured = False


def register_secret(value: Optional[str]) -> None:
    """Register a live secret value for exact-string scrubbing."""
    if not value:
        return
    v = str(value).strip()
    if len(v) < MIN_SECRET_LEN:
        return
    with _extra_lock:
        if v in _extra_secrets:
            return
        if len(_extra_secrets) >= MAX_REGISTERED_SECRETS:
            # Soft refuse growth — env keys still scrubbed via SECRET_ENV_KEYS
            return
        _extra_secrets.add(v)


def register_secrets(values: Iterable[Optional[str]]) -> None:
    for v in values:
        register_secret(v)


def _env_secret_values() -> list[str]:
    out: list[str] = []
    for key in SECRET_ENV_KEYS:
        raw = os.environ.get(key)
        if not raw:
            continue
        v = raw.strip()
        if len(v) >= MIN_SECRET_LEN:
            out.append(v)
    with _extra_lock:
        out.extend(_extra_secrets)
    # Longest first so shorter substrings don't leave remnants
    out.sort(key=len, reverse=True)
    # de-dupe preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for v in out:
        if v not in seen:
            seen.add(v)
            uniq.append(v)
    return uniq


def redact_text(text: Optional[str]) -> str:
    """Scrub secrets from an arbitrary string (logs, exceptions, audit)."""
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    if not text:
        return text
    truncated = False
    if len(text) > MAX_REDACT_CHARS:
        text = text[:MAX_REDACT_CHARS]
        truncated = True
    out = text
    for secret in _env_secret_values():
        if secret and secret in out:
            out = out.replace(secret, REDACTED)
    out = _RE_TG_BOT_URL.sub(rf"\1{REDACTED}", out)
    out = _RE_TG_TOKEN.sub(REDACTED, out)
    out = _RE_AUTH_HEADER.sub(rf"\1\2{REDACTED}", out)
    out = _RE_JSON_SECRET.sub(rf"\1{REDACTED}\3", out)
    if truncated:
        out = out + "...[redact_truncated]"
    return out


def redact_exc(exc: BaseException, *, max_len: int = 512) -> str:
    """Scrub + soft-truncate exception text for logs / API messages."""
    out = redact_text(str(exc))
    n = max(32, int(max_len))
    if len(out) > n:
        return out[:n] + "...[redact_truncated]"
    return out


def _redact_arg(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, BaseException):
        return redact_text(str(value))
    if isinstance(value, Mapping):
        return redact_mapping(value)
    if isinstance(value, (list, tuple)):
        seq = [_redact_arg(v) for v in value]
        return type(value)(seq) if not isinstance(value, list) else seq
    return value


def redact_mapping(data: Mapping[str, Any]) -> dict[str, Any]:
    """Deep-copy mapping with secret-bearing keys/values scrubbed."""
    sensitive_keys = {
        "api_key",
        "api_secret",
        "api_passphrase",
        "token",
        "bot_token",
        "password",
        "secret",
        "authorization",
        "x_radar_api_key",
        "kc_api_key",
        "kc_api_sign",
        "kc_api_passphrase",
        "telegram_bot_token",
        "radar_api_key",
        "kucoin_api_key",
        "kucoin_api_secret",
        "kucoin_api_passphrase",
    }
    out: dict[str, Any] = {}
    for k, v in data.items():
        key_norm = str(k).lower().replace("-", "_")
        if key_norm in sensitive_keys:
            out[str(k)] = REDACTED
            continue
        if isinstance(v, str):
            out[str(k)] = redact_text(v)
        elif isinstance(v, Mapping):
            out[str(k)] = redact_mapping(v)
        elif isinstance(v, list):
            out[str(k)] = [_redact_arg(x) for x in v]
        else:
            out[str(k)] = v
    return out


class SecretRedactFilter(logging.Filter):
    """Mutate log record msg/args before handlers format them."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = redact_text(record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {k: _redact_arg(v) for k, v in record.args.items()}
                elif isinstance(record.args, tuple):
                    record.args = tuple(_redact_arg(a) for a in record.args)
                else:
                    record.args = _redact_arg(record.args)
        except Exception:  # noqa: BLE001 — never break logging
            return True
        return True


class RedactingFormatter(logging.Formatter):
    """Final-line scrub including exception tracebacks."""

    def format(self, record: logging.LogRecord) -> str:
        try:
            text = super().format(record)
        except Exception:  # noqa: BLE001
            text = getattr(record, "msg", "") or ""
        return redact_text(text)


def configure_secure_logging(*, level: int = logging.INFO) -> None:
    """Install redacting formatter/filter on the root logger (idempotent)."""
    global _configured
    root = logging.getLogger()
    root.setLevel(level)
    fmt = RedactingFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    filt = SecretRedactFilter()

    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(fmt)
        handler.addFilter(filt)
        root.addHandler(handler)
    else:
        for handler in root.handlers:
            handler.setFormatter(fmt)
            # Avoid duplicate identical filters
            if not any(isinstance(f, SecretRedactFilter) for f in handler.filters):
                handler.addFilter(filt)

    if not any(isinstance(f, SecretRedactFilter) for f in root.filters):
        root.addFilter(filt)

    # httpx/httpcore DEBUG can dump URLs/headers — keep them quiet
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    # Pull current env secrets into the scrub set
    for key in SECRET_ENV_KEYS:
        register_secret(os.environ.get(key))

    _configured = True


def secure_logging_configured() -> bool:
    return _configured

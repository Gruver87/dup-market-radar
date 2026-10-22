from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from radar.config import ROOT
from radar.secrets_redact import redact_exc, register_secrets

logger = logging.getLogger("radar.trade.env")

# Fail-closed SSRF guard — signed KuCoin calls only to known HTTPS hosts.
_ALLOWED_KUCOIN_HOSTS = frozenset(
    {
        "api.kucoin.com",
        "openapi-v2.kucoin.com",
        "api-futures.kucoin.com",  # not used for spot orders; listed for clarity
    }
)
_DEFAULT_KUCOIN_BASE = "https://api.kucoin.com"
# Soft refuse absurd .env (DoS / accidental paste of dumps).
_ENV_MAX_BYTES = 65_536
_ENV_MAX_LINES = 512
_ENV_MAX_LINE_LEN = 4_096
# Bound credential string sizes before register_secret / HTTP headers.
_CRED_MIN_LEN = 8
_CRED_MAX_LEN = 256


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env loader (no dependency). Does not override existing env."""
    env_path = path or (ROOT / ".env")
    if not env_path.is_file():
        return
    try:
        size = env_path.stat().st_size
    except OSError as exc:
        logger.warning("dotenv stat failed: %s", redact_exc(exc, max_len=256))
        return
    if size > _ENV_MAX_BYTES:
        logger.error(
            "dotenv refused — file too large bytes=%s max=%s", size, _ENV_MAX_BYTES
        )
        return
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("dotenv read failed: %s", redact_exc(exc, max_len=256))
        return
    lines = text.splitlines()
    if len(lines) > _ENV_MAX_LINES:
        logger.error(
            "dotenv refused — too many lines=%s max=%s", len(lines), _ENV_MAX_LINES
        )
        return
    for line in lines:
        if len(line) > _ENV_MAX_LINE_LEN:
            logger.warning("dotenv skip oversized line len=%s", len(line))
            continue
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def validate_kucoin_base_url(raw: Optional[str]) -> tuple[str, Optional[str]]:
    """Return (safe_base_url, error). On error safe_base is empty string."""
    candidate = (raw or _DEFAULT_KUCOIN_BASE).strip() or _DEFAULT_KUCOIN_BASE
    try:
        parsed = urlparse(candidate)
    except Exception as exc:  # noqa: BLE001
        return "", f"KUCOIN_API_BASE parse failed: {redact_exc(exc, max_len=128)}"
    if parsed.scheme != "https":
        return "", "KUCOIN_API_BASE must be https"
    host = (parsed.hostname or "").lower()
    if host not in _ALLOWED_KUCOIN_HOSTS:
        return "", f"KUCOIN_API_BASE host refused: {host or '?'}"
    if parsed.username or parsed.password:
        return "", "KUCOIN_API_BASE must not embed credentials"
    # Origin only — drop path/query/fragment (SSRF / path injection)
    return f"https://{host}", None


def _bounded_cred(name: str, raw: Optional[str]) -> str:
    """Return credential or empty if length outside soft gate (fail-closed)."""
    v = (raw or "").strip()
    if not v:
        return ""
    if len(v) < _CRED_MIN_LEN or len(v) > _CRED_MAX_LEN:
        logger.error(
            "kucoin %s length refused len=%s (need %s..%s)",
            name,
            len(v),
            _CRED_MIN_LEN,
            _CRED_MAX_LEN,
        )
        return ""
    return v


def _secret_or_empty(logical_id: str) -> str:
    from radar.secret_mgmt import SecretNotFoundError, get_secret_manager

    try:
        return get_secret_manager().get_secret(logical_id)
    except SecretNotFoundError:
        return ""


def kucoin_creds() -> dict[str, str]:
    load_dotenv()
    from radar.secret_mgmt import (
        SECRET_KUCOIN_API_KEY,
        SECRET_KUCOIN_API_PASSPHRASE,
        SECRET_KUCOIN_API_SECRET,
    )

    base, base_err = validate_kucoin_base_url(os.environ.get("KUCOIN_API_BASE"))
    creds = {
        "api_key": _bounded_cred("api_key", _secret_or_empty(SECRET_KUCOIN_API_KEY)),
        "api_secret": _bounded_cred(
            "api_secret", _secret_or_empty(SECRET_KUCOIN_API_SECRET)
        ),
        "api_passphrase": _bounded_cred(
            "api_passphrase", _secret_or_empty(SECRET_KUCOIN_API_PASSPHRASE)
        ),
        "base_url": base,
        "base_url_error": base_err or "",
    }
    register_secrets(
        (creds["api_key"], creds["api_secret"], creds["api_passphrase"])
    )
    if base_err:
        logger.error("kucoin base url refused: %s", base_err)
    return creds


def trading_configured() -> bool:
    c = kucoin_creds()
    return bool(
        c["api_key"]
        and c["api_secret"]
        and c["api_passphrase"]
        and c["base_url"]
        and not c.get("base_url_error")
    )

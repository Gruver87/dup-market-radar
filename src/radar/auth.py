"""Optional API key for trade mutations — via SecretManagerPort (never hardcoded)."""

from __future__ import annotations

import hmac
import os
from typing import Optional

from radar.secret_mgmt import (
    SECRET_RADAR_API_KEY,
    SecretNotFoundError,
    get_secret_manager,
)
from radar.trade.env_load import load_dotenv

# Bound header compare cost — refuse oversized X-Radar-Api-Key before digest.
API_KEY_MAX_LEN = 256
API_KEY_MIN_LEN = 8


def _truthy(raw: Optional[str]) -> bool:
    return (raw or "").strip().lower() in {"1", "true", "yes", "on"}


def radar_api_key() -> Optional[str]:
    """Return configured API key or None if gate disabled / too short."""
    load_dotenv()
    try:
        key = get_secret_manager().get_secret(SECRET_RADAR_API_KEY).strip()
    except SecretNotFoundError:
        return None
    if len(key) < API_KEY_MIN_LEN or len(key) > API_KEY_MAX_LEN:
        # Treat misconfigured key as unset — api_key_gate_ok will fail when required.
        return None
    return key


def require_api_key_env() -> bool:
    """RADAR_REQUIRE_API_KEY — fail-closed when key must be set (docker/prod)."""
    load_dotenv()
    return _truthy(os.environ.get("RADAR_REQUIRE_API_KEY"))


def api_key_gate_ok() -> bool:
    """False when require=true but RADAR_API_KEY is unset/invalid length."""
    if require_api_key_env() and radar_api_key() is None:
        return False
    return True


def api_key_required() -> bool:
    """True when a key is configured (clients must send X-Radar-Api-Key)."""
    return radar_api_key() is not None


def verify_api_key(provided: Optional[str]) -> bool:
    expected = radar_api_key()
    if expected is None:
        return True  # gate off (unless require_api_key_env — checked separately)
    if not provided:
        return False
    raw = provided.strip()
    # Soft-refuse oversized header before compare_digest allocates work.
    if len(raw) < API_KEY_MIN_LEN or len(raw) > API_KEY_MAX_LEN:
        return False
    return hmac.compare_digest(raw, expected)

"""Env / K8s-injected env secret adapter."""

from __future__ import annotations

import os
from typing import Dict, Optional

from radar.secret_mgmt.ports import (
    SECRET_KUCOIN_API_KEY,
    SECRET_KUCOIN_API_PASSPHRASE,
    SECRET_KUCOIN_API_SECRET,
    SECRET_RADAR_API_KEY,
    SECRET_TELEGRAM_BOT_TOKEN,
    SECRET_WEBHOOK_URL,
    SecretManagerPort,
    SecretNotFoundError,
)
from radar.secrets_redact import register_secret

# Logical id → process env var (existing Radar contract).
_ENV_MAP: Dict[str, str] = {
    SECRET_RADAR_API_KEY: "RADAR_API_KEY",
    SECRET_KUCOIN_API_KEY: "KUCOIN_API_KEY",
    SECRET_KUCOIN_API_SECRET: "KUCOIN_API_SECRET",
    SECRET_KUCOIN_API_PASSPHRASE: "KUCOIN_API_PASSPHRASE",
    SECRET_TELEGRAM_BOT_TOKEN: "TELEGRAM_BOT_TOKEN",
    SECRET_WEBHOOK_URL: "RADAR_WEBHOOK_URL",
}


class EnvSecretAdapter:
    """Resolve secrets from os.environ only (after optional dotenv load)."""

    def get_secret(self, name: str) -> str:
        env_key = _ENV_MAP.get(name)
        if not env_key:
            raise SecretNotFoundError(name)
        val = (os.environ.get(env_key) or "").strip()
        if not val:
            # Discord alias for webhook
            if name == SECRET_WEBHOOK_URL:
                val = (os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()
            if not val:
                raise SecretNotFoundError(name)
        register_secret(val)
        return val

    def has_secret(self, name: str) -> bool:
        try:
            self.get_secret(name)
            return True
        except SecretNotFoundError:
            return False

    def backend_name(self) -> str:
        return "env"

    def peek_optional(self, name: str) -> Optional[str]:
        try:
            return self.get_secret(name)
        except SecretNotFoundError:
            return None


def env_var_for(logical_id: str) -> Optional[str]:
    return _ENV_MAP.get(logical_id)


# Type check helper
def _as_port(a: EnvSecretAdapter) -> SecretManagerPort:
    return a

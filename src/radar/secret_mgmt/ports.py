"""SecretManagerPort — logical secret ids (ADR 0015 spirit, Radar-scoped)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

# Stable logical ids — adapters map to env / file keys. Never log values.
SECRET_RADAR_API_KEY = "radar.api_key"
SECRET_KUCOIN_API_KEY = "kucoin.api_key"
SECRET_KUCOIN_API_SECRET = "kucoin.api_secret"
SECRET_KUCOIN_API_PASSPHRASE = "kucoin.api_passphrase"
SECRET_TELEGRAM_BOT_TOKEN = "telegram.bot_token"
SECRET_WEBHOOK_URL = "radar.webhook_url"


@runtime_checkable
class SecretManagerPort(Protocol):
    def get_secret(self, name: str) -> str:
        ...

    def has_secret(self, name: str) -> bool:
        ...

    def backend_name(self) -> str:
        ...


class SecretNotFoundError(LookupError):
    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"secret_not_found:{name}")


class SecretBackendRefused(RuntimeError):
    """Prod refuse of unsafe backends (file / null)."""


class NullSecretManager:
    """Empty store for unit tests."""

    def get_secret(self, name: str) -> str:
        raise SecretNotFoundError(name)

    def has_secret(self, name: str) -> bool:
        return False

    def backend_name(self) -> str:
        return "null"

    def __repr__(self) -> str:
        return "NullSecretManager(redacted)"

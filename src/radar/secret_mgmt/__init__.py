"""Secret management — SecretManagerPort + env/file adapters."""

from radar.secret_mgmt.factory import (
    build_secret_manager,
    deployment_mode,
    get_secret_manager,
    reset_secret_manager_for_tests,
    secrets_status,
)
from radar.secret_mgmt.ports import (
    SECRET_KUCOIN_API_KEY,
    SECRET_KUCOIN_API_PASSPHRASE,
    SECRET_KUCOIN_API_SECRET,
    SECRET_RADAR_API_KEY,
    SECRET_TELEGRAM_BOT_TOKEN,
    SECRET_WEBHOOK_URL,
    SecretBackendRefused,
    SecretManagerPort,
    SecretNotFoundError,
)

__all__ = [
    "SECRET_KUCOIN_API_KEY",
    "SECRET_KUCOIN_API_PASSPHRASE",
    "SECRET_KUCOIN_API_SECRET",
    "SECRET_RADAR_API_KEY",
    "SECRET_TELEGRAM_BOT_TOKEN",
    "SECRET_WEBHOOK_URL",
    "SecretBackendRefused",
    "SecretManagerPort",
    "SecretNotFoundError",
    "build_secret_manager",
    "deployment_mode",
    "get_secret_manager",
    "reset_secret_manager_for_tests",
    "secrets_status",
]

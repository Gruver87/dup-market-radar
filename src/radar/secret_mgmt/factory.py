"""Build SecretManagerPort — fail-closed on unsafe prod backends."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from radar.config import ROOT
from radar.secret_mgmt.env_adapter import EnvSecretAdapter
from radar.secret_mgmt.file_adapter import FileSecretAdapter
from radar.secret_mgmt.ports import SecretBackendRefused, SecretManagerPort
from radar.trade.env_load import load_dotenv

_MANAGER: Optional[SecretManagerPort] = None


def deployment_mode() -> str:
    """lab (default) | prod — prod refuses file secret backend."""
    load_dotenv()
    raw = (os.environ.get("RADAR_DEPLOYMENT_MODE") or "").strip().lower()
    if raw in {"prod", "production"}:
        return "prod"
    try:
        from radar.config import load_config

        cfg = load_config()
        mode = str((cfg.get("deployment_mode") or "lab")).strip().lower()
        if mode in {"prod", "production"}:
            return "prod"
    except Exception:  # noqa: BLE001
        pass
    return "lab"


def secret_backend_name() -> str:
    load_dotenv()
    env = (os.environ.get("RADAR_SECRET_BACKEND") or "").strip().lower()
    if env in {"env", "file"}:
        return env
    try:
        from radar.config import load_config

        cfg = (load_config().get("secrets") or {})
        b = str(cfg.get("backend") or "env").strip().lower()
        if b in {"env", "file"}:
            return b
    except Exception:  # noqa: BLE001
        pass
    return "env"


def build_secret_manager(*, force_backend: Optional[str] = None) -> SecretManagerPort:
    """Construct adapter. Prod + file → SecretBackendRefused (fail-closed)."""
    load_dotenv()
    backend = (force_backend or secret_backend_name()).strip().lower()
    mode = deployment_mode()
    if backend == "file":
        if mode == "prod":
            raise SecretBackendRefused(
                "RADAR_SECRET_BACKEND=file refused when deployment_mode=prod "
                "(use env / K8s secrets)"
            )
        path_raw = (os.environ.get("RADAR_SECRETS_FILE") or "").strip()
        if not path_raw:
            try:
                from radar.config import load_config

                path_raw = str(
                    ((load_config().get("secrets") or {}).get("file_path") or "")
                ).strip()
            except Exception:  # noqa: BLE001
                path_raw = ""
        path = Path(path_raw) if path_raw else (ROOT / "data" / "secrets.json")
        return FileSecretAdapter(path)
    if backend in {"null", "none"}:
        if mode == "prod":
            raise SecretBackendRefused("null secret backend refused in prod")
        from radar.secret_mgmt.ports import NullSecretManager

        return NullSecretManager()
    # default env
    return EnvSecretAdapter()


def get_secret_manager() -> SecretManagerPort:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = build_secret_manager()
    return _MANAGER


def reset_secret_manager_for_tests() -> None:
    global _MANAGER
    _MANAGER = None


def secrets_status() -> Dict[str, Any]:
    try:
        mgr = get_secret_manager()
        return {
            "ok": True,
            "backend": mgr.backend_name(),
            "deployment_mode": deployment_mode(),
        }
    except SecretBackendRefused as exc:
        return {
            "ok": False,
            "error": str(exc)[:200],
            "deployment_mode": deployment_mode(),
            "backend": secret_backend_name(),
        }

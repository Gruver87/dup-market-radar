"""File-backed secrets — lab/dev only. Prod must refuse this backend."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from radar.secret_mgmt.ports import SecretManagerPort, SecretNotFoundError
from radar.secrets_redact import register_secret

_MAX_FILE_BYTES = 65_536


class FileSecretAdapter:
    """JSON map of logical_id → secret string. Never use under deployment_mode=prod."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._cache: Dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.is_file():
            self._cache = {}
            return
        size = self._path.stat().st_size
        if size > _MAX_FILE_BYTES:
            raise ValueError(f"secrets file too large: {size}")
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("secrets file must be a JSON object")
        out: Dict[str, str] = {}
        for k, v in list(raw.items())[:64]:
            if isinstance(k, str) and isinstance(v, str) and v.strip():
                out[k.strip()] = v.strip()
        self._cache = out

    def get_secret(self, name: str) -> str:
        val = self._cache.get(name)
        if not val:
            raise SecretNotFoundError(name)
        register_secret(val)
        return val

    def has_secret(self, name: str) -> bool:
        return bool(self._cache.get(name))

    def backend_name(self) -> str:
        return "file"

    def status(self) -> Dict[str, Any]:
        return {
            "backend": "file",
            "path_set": bool(self._path),
            "keys": len(self._cache),
        }


def _as_port(a: FileSecretAdapter) -> SecretManagerPort:
    return a

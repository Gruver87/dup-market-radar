"""Optional JSON structured logging (LOG_JSON=1)."""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def __init__(self, *, deployment_mode: str = "lab") -> None:
        super().__init__()
        self.deployment_mode = deployment_mode

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "product": "dup-market-radar",
            "deployment_mode": self.deployment_mode,
        }
        rid = getattr(record, "request_id", None)
        if rid:
            payload["request_id"] = str(rid)[:64]
        if record.exc_info and record.exc_info[1]:
            payload["exception"] = self.formatException(record.exc_info)[:2000]
        return json.dumps(payload, ensure_ascii=False)


def log_json_enabled() -> bool:
    return (os.environ.get("RADAR_LOG_JSON") or os.environ.get("LOG_JSON") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def configure_json_logging_if_enabled(*, deployment_mode: str = "lab") -> bool:
    """If RADAR_LOG_JSON/LOG_JSON set, replace root stream handlers with JsonFormatter.

    Returns True when JSON mode applied. Soft: never raises.
    """
    if not log_json_enabled():
        return False
    try:
        root = logging.getLogger()
        fmt = JsonFormatter(deployment_mode=deployment_mode)
        # Replace stream handlers only; keep RedactingFilter if present on logger tree.
        for h in list(root.handlers):
            if isinstance(h, logging.StreamHandler) and not isinstance(
                h, logging.FileHandler
            ):
                h.setFormatter(fmt)
        if not root.handlers:
            sh = logging.StreamHandler(sys.stdout)
            sh.setFormatter(fmt)
            root.addHandler(sh)
        return True
    except Exception:  # noqa: BLE001
        return False

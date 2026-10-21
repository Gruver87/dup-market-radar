"""Rust hot-path bridge (radar_native) — same hybrid idea as abs_native on the L1 pin."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional, Tuple

from radar.secrets_redact import redact_text

logger = logging.getLogger("radar.native")

_NATIVE = None
_LOAD_ERROR: Optional[str] = None


def _try_load():
    global _NATIVE, _LOAD_ERROR
    if _NATIVE is not None or _LOAD_ERROR is not None:
        return
    try:
        import radar_native as mod  # type: ignore

        _NATIVE = mod
        logger.info("radar_native loaded version=%s", getattr(mod, "native_version", lambda: "?")())
    except Exception as exc:  # noqa: BLE001
        _LOAD_ERROR = redact_text(str(exc))[:256]
        logger.warning(
            "radar_native unavailable — Python scorers: %s", _LOAD_ERROR
        )


def native_available() -> bool:
    _try_load()
    return _NATIVE is not None


def native_status() -> Dict[str, Any]:
    _try_load()
    if _NATIVE is None:
        return {"available": False, "error": _LOAD_ERROR, "backend": "python"}
    return {
        "available": True,
        "backend": "radar_native",
        "version": getattr(_NATIVE, "native_version", lambda: "unknown")(),
        "error": None,
    }


def score_universe_native(
    universe: Dict[str, Any], cfg: Dict[str, Any], top_n: int
) -> Tuple[Dict[str, Any], str]:
    """Returns (sectors_dict_with_meta, backend_label). Raises if native missing."""
    _try_load()
    if _NATIVE is None:
        raise RuntimeError(_LOAD_ERROR or "radar_native not loaded")
    raw = _NATIVE.score_universe_json(json.dumps(universe), json.dumps(cfg), int(top_n))
    parsed = json.loads(raw)
    sectors = parsed.get("sectors") or {}
    return sectors, str(parsed.get("backend") or "radar_native")

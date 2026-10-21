"""Sector scoring engine — prefer Rust hot path; fail-closed when configured."""

from __future__ import annotations

import logging
from typing import Any, Dict, Tuple

from radar.native_bridge import native_available, score_universe_native
from radar.rank.topn import rank_top
from radar.score.futures import score_futures
from radar.score.listings import score_listings
from radar.score.memes import score_memes
from radar.score.spot_alts import score_spot_alts
from radar.score.stables import score_stables
from radar.score.whales import score_whales
from radar.secrets_redact import redact_exc

logger = logging.getLogger("radar.score.engine")


class NativeScoreError(RuntimeError):
    """Raised when native.prefer + fail_closed and Rust path cannot run."""


def _assert_int_money(sectors: Dict[str, Any]) -> None:
    """Refuse float legacy score fields from native (fail-closed honesty)."""
    for name, rows in sectors.items():
        if not isinstance(rows, list):
            continue
        for row in rows[:3]:
            if not isinstance(row, dict):
                continue
            if "score" in row and "score_centi" not in row:
                raise RuntimeError(f"native {name}: legacy float score without score_centi")
            if row.get("score_centi") is not None and not isinstance(row["score_centi"], int):
                raise RuntimeError(f"native {name}: score_centi must be int")


def _python_sectors(universe: Dict[str, Any], cfg: Dict[str, Any], top_n: int) -> Dict[str, Any]:
    sectors = {
        "spot_alts": rank_top(score_spot_alts(universe, cfg), top_n),
        "futures": rank_top(score_futures(universe, cfg), top_n),
        "memes": rank_top(score_memes(universe, cfg), top_n),
        "stables": rank_top(score_stables(universe, cfg), top_n),
        "listings": rank_top(score_listings(universe, cfg), top_n),
        "whales": rank_top(score_whales(universe, cfg), top_n),
    }
    fut = sectors["futures"]
    sectors["futures_long"] = [c for c in fut if c.get("setup") == "long"][:top_n]
    sectors["futures_short"] = [c for c in fut if c.get("setup") == "short"][:top_n]
    return sectors


def score_sectors(
    universe: Dict[str, Any], cfg: Dict[str, Any], top_n: int
) -> Tuple[Dict[str, Any], str]:
    native_cfg = cfg.get("native") or {}
    prefer = bool(native_cfg.get("prefer", True))
    # Default: fail-closed whenever prefer is on (no silent rank swap for auto-trade)
    fail_closed = bool(native_cfg.get("fail_closed", prefer))

    if prefer:
        if not native_available():
            msg = "native.prefer=true but radar_native unavailable"
            if fail_closed:
                raise NativeScoreError(msg)
            logger.warning("%s — Python scorers", msg)
            return _python_sectors(universe, cfg, top_n), "python_native_missing"
        try:
            sectors, backend = score_universe_native(universe, cfg, top_n)
            _assert_int_money(sectors)
            return sectors, backend
        except NativeScoreError:
            raise
        except Exception as exc:  # noqa: BLE001
            safe = redact_exc(exc, max_len=256)
            if fail_closed:
                raise NativeScoreError(f"radar_native failed: {safe}") from exc
            logger.warning("radar_native failed — Python scorers: %s", safe)
            return _python_sectors(universe, cfg, top_n), "python_after_native_fail"

    return _python_sectors(universe, cfg, top_n), "python"

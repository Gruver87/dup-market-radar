"""Rotating JSONL audit log — size-capped, fail-closed on write errors."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from radar.secrets_redact import redact_exc, redact_mapping

logger = logging.getLogger("radar.audit")

# Soft refuse absurd single-line records (memory / disk flood).
MAX_AUDIT_LINE_CHARS = 65_536


class AuditLogError(RuntimeError):
    pass


def rotate_jsonl(path: Path, *, max_bytes: int, keep: int = 5) -> Optional[Path]:
    """If path exceeds max_bytes, rotate to path.1, path.2, … Keep newest `keep`."""
    if max_bytes <= 0 or keep < 0:
        return None
    if not path.exists():
        return None
    try:
        size = path.stat().st_size
    except OSError as exc:
        logger.warning(
            "audit stat failed %s: %s", path.name, redact_exc(exc, max_len=256)
        )
        return None
    if size < max_bytes:
        return None

    if keep == 0:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise AuditLogError(
                f"cannot truncate {path.name}: {redact_exc(exc, max_len=128)}"
            ) from exc
        return None

    # Drop slots at/above keep (and stale higher indices)
    for i in range(keep, keep + 64):
        stale = path.with_name(f"{path.name}.{i}")
        try:
            stale.unlink(missing_ok=True)
        except OSError as exc:
            logger.debug(
                "audit stale unlink skipped %s: %s",
                stale.name,
                redact_exc(exc, max_len=128),
            )

    # Shift .N → .(N+1) from high to low so we don't clobber
    for i in range(keep - 1, 0, -1):
        src = path.with_name(f"{path.name}.{i}")
        dst = path.with_name(f"{path.name}.{i + 1}")
        if src.exists():
            try:
                src.replace(dst)
            except OSError as exc:
                logger.warning(
                    "audit rotate shift failed: %s", redact_exc(exc, max_len=256)
                )

    rotated = path.with_name(f"{path.name}.1")
    try:
        path.replace(rotated)
    except OSError as exc:
        raise AuditLogError(
            f"cannot rotate {path.name}: {redact_exc(exc, max_len=128)}"
        ) from exc
    return rotated


def append_jsonl(
    path: Path,
    record: Dict[str, Any],
    *,
    max_bytes: int = 5_242_880,
    keep: int = 5,
) -> None:
    """Append one JSON object as a line. Rotates when over max_bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rotate_jsonl(path, max_bytes=max_bytes, keep=keep)
    safe = redact_mapping(record)
    line = json.dumps(safe, ensure_ascii=False) + "\n"
    if len(line) > MAX_AUDIT_LINE_CHARS:
        keys = list(safe.keys())[:32] if isinstance(safe, dict) else []
        line = (
            json.dumps(
                {
                    "_audit_truncated": True,
                    "keys": keys,
                    "orig_chars": len(line),
                },
                ensure_ascii=False,
            )
            + "\n"
        )
        logger.warning(
            "audit line truncated path=%s orig_chars=%s",
            path.name,
            len(json.dumps(safe, ensure_ascii=False)) + 1,
        )
    try:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError as exc:
        raise AuditLogError(
            f"audit append failed {path.name}: {redact_exc(exc, max_len=128)}"
        ) from None

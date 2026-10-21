"""Atomic JSON persistence — checksum sidecar + rotating backups."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from radar.secrets_redact import redact_exc, redact_text

logger = logging.getLogger("radar.persist")

# Soft refuse absurd on-disk JSON (positions / watches / prefs) — memory DoS.
DEFAULT_MAX_JSON_BYTES = 8_388_608  # 8 MiB


class PersistError(RuntimeError):
    pass


def sha256_text(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def checksum_path(path: Path) -> Path:
    return path.with_name(path.name + ".sha256")


def backup_dir(path: Path) -> Path:
    return path.parent / "backups"


def rotate_backups(path: Path, *, keep: int = 5) -> Optional[Path]:
    """Copy current file (+ checksum) into data/backups/ before overwrite."""
    if not path.exists() or keep <= 0:
        return None
    bdir = backup_dir(path)
    bdir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    dest = bdir / f"{path.stem}_{stamp}{path.suffix}"
    try:
        shutil.copy2(path, dest)
        csum = checksum_path(path)
        if csum.exists():
            shutil.copy2(csum, bdir / f"{path.stem}_{stamp}{path.suffix}.sha256")
    except OSError as exc:
        logger.warning(
            "backup copy failed %s: %s", path.name, redact_exc(exc, max_len=256)
        )
        return None
    _prune_glob(bdir, f"{path.stem}_*{path.suffix}", keep=keep)
    _prune_glob(bdir, f"{path.stem}_*{path.suffix}.sha256", keep=keep)
    return dest


def _prune_glob(bdir: Path, pattern: str, *, keep: int) -> None:
    files: List[Path] = sorted(
        [p for p in bdir.glob(pattern) if p.is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in files[keep:]:
        try:
            old.unlink(missing_ok=True)
        except OSError as exc:
            logger.debug(
                "backup prune unlink skipped %s: %s",
                old.name,
                redact_exc(exc, max_len=128),
            )


def _write_text_fsync(path: Path, text: str) -> None:
    """Write then fsync so crash mid-write cannot leave half-flushed JSON."""
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())


def atomic_write_json(
    path: Path,
    data: Any,
    *,
    with_checksum: bool = True,
    backup_keep: int = 5,
) -> str:
    """Write JSON atomically. Returns sha256 hex of canonical payload."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup_keep > 0:
        rotate_backups(path, keep=backup_keep)
    payload = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True)
    if len(payload.encode("utf-8")) > DEFAULT_MAX_JSON_BYTES:
        raise PersistError(
            f"json payload too large for {path.name} "
            f"(>{DEFAULT_MAX_JSON_BYTES} bytes) — soft refuse"
        )
    digest = sha256_text(payload)
    tmp = path.with_suffix(path.suffix + ".tmp")
    _write_text_fsync(tmp, payload)
    tmp.replace(path)
    if with_checksum:
        csum = checksum_path(path)
        ctmp = Path(str(csum) + ".tmp")
        _write_text_fsync(ctmp, digest + "\n")
        ctmp.replace(csum)
    return digest


def read_json_object(
    path: Path,
    *,
    require_checksum: bool = False,
    max_bytes: int = DEFAULT_MAX_JSON_BYTES,
) -> Tuple[Dict[str, Any], Optional[str], Dict[str, Any]]:
    """Return (data, error, meta).

    meta: sha256, integrity = ok|missing_checksum|mismatch|absent|corrupt|too_large
    """
    meta: Dict[str, Any] = {"sha256": None, "integrity": "absent"}
    if not path.exists():
        return {}, None, meta
    try:
        size = path.stat().st_size
    except OSError as exc:
        msg = f"stat failed {path.name}: {redact_exc(exc, max_len=128)}"
        logger.error(msg)
        meta["integrity"] = "corrupt"
        return {}, msg, meta
    limit = max(1024, int(max_bytes))
    if size > limit:
        msg = f"json too large {path.name}: {size} > {limit}"
        logger.error(msg)
        meta["integrity"] = "too_large"
        return {}, msg, meta
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        msg = f"corrupt json {path.name}: {redact_exc(exc, max_len=128)}"
        logger.error(msg)
        meta["integrity"] = "corrupt"
        return {}, msg, meta
    if not isinstance(data, dict):
        msg = f"corrupt json {path.name}: expected object"
        logger.error(msg)
        meta["integrity"] = "corrupt"
        return {}, msg, meta

    canonical = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True)
    actual = sha256_text(canonical)
    meta["sha256"] = actual

    csum_file = checksum_path(path)
    if not csum_file.exists():
        meta["integrity"] = "missing_checksum"
        if require_checksum:
            return {}, f"missing checksum for {path.name}", meta
        return data, None, meta

    try:
        csum_size = csum_file.stat().st_size
    except OSError as exc:
        msg = f"checksum stat failed {path.name}: {redact_exc(exc, max_len=128)}"
        meta["integrity"] = "corrupt"
        return {}, msg, meta
    # Hex sha256 is 64 chars; bound absurd sidecars
    if csum_size > 512:
        msg = f"checksum too large {path.name}: {csum_size}"
        meta["integrity"] = "corrupt"
        return {}, msg, meta
    try:
        expected = csum_file.read_text(encoding="utf-8").strip().split()[0]
    except Exception as exc:  # noqa: BLE001
        msg = f"checksum unreadable {path.name}: {redact_exc(exc, max_len=128)}"
        meta["integrity"] = "corrupt"
        return {}, msg, meta
    if len(expected) < 32 or len(expected) > 128:
        msg = f"checksum digest length refused {path.name}"
        meta["integrity"] = "corrupt"
        return {}, msg, meta

    raw_digest = sha256_text(raw)
    if hmac.compare_digest(expected, actual) or hmac.compare_digest(expected, raw_digest):
        meta["integrity"] = "ok"
        return data, None, meta

    msg = f"checksum mismatch {path.name}"
    logger.error(msg)
    meta["integrity"] = "mismatch"
    return {}, msg, meta

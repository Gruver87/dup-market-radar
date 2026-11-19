"""Optional bridge to Rust `radar-ingest-binance` binary (watch-only).

Does not replace HTTP poll. Used for smoke / future snapshot merge experiments.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
NATIVE = ROOT / "native"


def ingest_venue_ticks(
    *,
    venue: str = "binance",
    seconds: int = 3,
    symbols: str = "BTCUSDT",
    max_ticks: int = 10,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Run release binary; return {ok, ticks[], error?}."""
    cargo = shutil.which("cargo")
    if not cargo:
        return {"ok": False, "error": "cargo_not_found", "ticks": []}
    if not (NATIVE / "Cargo.toml").is_file():
        return {"ok": False, "error": "native_workspace_missing", "ticks": []}
    v = (venue or "binance").strip().lower()
    if v == "bybit":
        bin_name = "radar-ingest-bybit"
    elif v == "kucoin":
        bin_name = "radar-ingest-kucoin"
    else:
        bin_name = "radar-ingest-binance"
    cmd = [
        cargo,
        "run",
        "-p",
        "radar_ingest",
        "--release",
        "--bin",
        bin_name,
        "--manifest-path",
        str(NATIVE / "Cargo.toml"),
        "--",
        "--seconds",
        str(int(seconds)),
        "--symbols",
        symbols,
        "--max-ticks",
        str(int(max_ticks)),
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout", "ticks": []}
    ticks: list[dict[str, Any]] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("price_i") is not None:
            ticks.append(row)
    if proc.returncode != 0 and not ticks:
        err = (proc.stderr or "")[-400:]
        return {"ok": False, "error": f"exit_{proc.returncode}:{err}", "ticks": []}
    return {
        "ok": bool(ticks),
        "ticks": ticks,
        "n": len(ticks),
        "venue": v,
        "stderr_tail": (proc.stderr or "")[-200:],
    }


def ingest_binance_ticks(
    *,
    seconds: int = 3,
    symbols: str = "BTCUSDT",
    max_ticks: int = 10,
    timeout: float = 60.0,
) -> dict[str, Any]:
    return ingest_venue_ticks(
        venue="binance",
        seconds=seconds,
        symbols=symbols,
        max_ticks=max_ticks,
        timeout=timeout,
    )

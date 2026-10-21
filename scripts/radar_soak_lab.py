#!/usr/bin/env python3
"""Multi-cycle live soak lab — evidence pack with hard_fails counter.

NOT a 48h claim unless you actually run ~48h of cycles. Default is a short
lab soak (e.g. 3–10 cycles) for gate evidence.

Usage:
  python scripts/radar_soak_lab.py --cycles 3 --interval-sec 30
  python scripts/radar_soak_lab.py --cycles 5 --retain-keep 20

Exit 1 if hard_fails > 0.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _one_cycle(force: bool = True) -> dict:
    from radar.config import load_config
    from radar.ingest.sector_poll import poll_state
    from radar.pipeline import run_scan

    cfg = dict(load_config())
    # Soft native for lab hosts without wheel — still records backend honesty
    cfg["native"] = {**(cfg.get("native") or {}), "fail_closed": False}
    poll_state().reset()
    t0 = int(time.time() * 1000)
    snap = run_scan(cfg, force=force)
    elapsed = int(time.time() * 1000) - t0
    ok = bool(snap.get("ok"))
    mtf = snap.get("mtf_ensure_ok")
    hard = False
    reasons = []
    if not ok:
        hard = True
        reasons.append("ok=false")
    if mtf is False:
        hard = True
        reasons.append("mtf_ensure_ok=false")
    errs = list(snap.get("errors") or [])[:8]
    if any(str(e).startswith("mtf_ensure_failed") for e in errs):
        hard = True
        if "mtf_ensure_failed" not in reasons:
            reasons.append("mtf_ensure_failed")
    return {
        "at_ms": int(time.time() * 1000),
        "ok": ok,
        "mtf_ensure_ok": mtf,
        "score_backend": snap.get("score_backend"),
        "elapsed_ms": snap.get("elapsed_ms") or elapsed,
        "universe_size": snap.get("universe_size"),
        "hard_fail": hard,
        "reasons": reasons,
        "errors": [str(e)[:256] for e in errs],
        "short_window_ensure": snap.get("short_window_ensure"),
    }


def run_soak(
    *,
    cycles: int,
    interval_sec: float,
    retain_keep: int,
) -> dict:
    cycles = max(1, int(cycles))
    results = []
    hard_fails = 0
    for i in range(cycles):
        try:
            row = _one_cycle(force=True)
        except Exception as exc:  # noqa: BLE001
            from radar.secrets_redact import redact_exc

            row = {
                "at_ms": int(time.time() * 1000),
                "ok": False,
                "hard_fail": True,
                "reasons": ["exception"],
                "errors": [redact_exc(exc, max_len=256)],
            }
        if row.get("hard_fail"):
            hard_fails += 1
        results.append(row)
        print(
            json.dumps(
                {
                    "cycle": i + 1,
                    "hard_fail": row.get("hard_fail"),
                    "ok": row.get("ok"),
                    "mtf_ensure_ok": row.get("mtf_ensure_ok"),
                    "elapsed_ms": row.get("elapsed_ms"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        if i + 1 < cycles and interval_sec > 0:
            time.sleep(float(interval_sec))

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    pack = {
        "product": "DUP Market Radar",
        "pack_kind": "soak_lab",
        "generated_at_utc": ts,
        "disclaimer": (
            "Evidence only — not financial advice; author accepts no liability. "
            "Not a 48h soak claim unless duration≈48h and hard_fails=0."
        ),
        "cycles": cycles,
        "interval_sec": interval_sec,
        "hard_fails": hard_fails,
        "results": results,
    }
    ev_dir = ROOT / "data" / "evidence"
    ev_dir.mkdir(parents=True, exist_ok=True)
    path = ev_dir / f"soak_lab_{ts}.json"
    path.write_text(json.dumps(pack, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    latest = ev_dir / "LATEST_SOAK.json"
    latest.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

    # Retention
    sys.path.insert(0, str(ROOT / "scripts"))
    from evidence_retention import retain  # type: ignore

    retention = retain(keep=retain_keep)
    pack["evidence_pack"] = str(path)
    pack["retention"] = retention
    print(json.dumps({"summary": {"hard_fails": hard_fails, "cycles": cycles, "pack": str(path)}}, indent=2))
    return pack


def main() -> int:
    p = argparse.ArgumentParser(description="DUP Market Radar soak lab")
    p.add_argument("--cycles", type=int, default=3, help="number of force scans (default 3)")
    p.add_argument("--interval-sec", type=float, default=45.0, help="sleep between cycles")
    p.add_argument("--retain-keep", type=int, default=20, help="evidence packs to keep")
    args = p.parse_args()
    pack = run_soak(
        cycles=args.cycles,
        interval_sec=args.interval_sec,
        retain_keep=args.retain_keep,
    )
    return 0 if int(pack.get("hard_fails") or 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

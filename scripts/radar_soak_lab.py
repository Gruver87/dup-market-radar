#!/usr/bin/env python3
"""Multi-cycle live soak lab — evidence pack with hard_fails counter.

NOT a 48h claim unless you actually run ~48h of cycles. Default is a short
lab soak (e.g. 3–10 cycles) for gate evidence.

Usage:
  python scripts/radar_soak_lab.py --cycles 3 --interval-sec 30
  python scripts/radar_soak_lab.py --cycles 5 --retain-keep 20
  python scripts/radar_soak_lab.py --duration-hours 48 --interval-sec 300

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
    from radar.native_bridge import native_available

    # Prefer native fail-closed when the wheel is importable (pre-soak / industrial).
    # Without wheel: soft prefer=false so lab soak is not hard_fail every cycle.
    if native_available():
        cfg["native"] = {
            **(cfg.get("native") or {}),
            "prefer": True,
            "fail_closed": True,
        }
    else:
        cfg["native"] = {
            **(cfg.get("native") or {}),
            "prefer": False,
            "fail_closed": False,
        }
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
    swe = snap.get("short_window_ensure") or {}
    native_cfg = cfg.get("native") or {}
    return {
        "at_ms": int(time.time() * 1000),
        "ok": ok,
        "mtf_ensure_ok": mtf,
        "score_backend": snap.get("score_backend"),
        "native_prefer": bool(native_cfg.get("prefer")),
        "native_fail_closed": bool(native_cfg.get("fail_closed")),
        "native": snap.get("native"),
        "elapsed_ms": snap.get("elapsed_ms") or elapsed,
        "universe_size": snap.get("universe_size"),
        "hard_fail": hard,
        "reasons": reasons,
        "errors": [str(e)[:256] for e in errs],
        "short_window_ensure": {
            k: swe.get(k)
            for k in (
                "spot_fill_ratio",
                "fut_fill_ratio",
                "list_spot_fill_ratio",
                "list_fut_fill_ratio",
                "meme_cex_fill_ratio",
                "meme_cex_targets",
                "rounds",
            )
            if k in swe or swe.get(k) is not None
        },
    }


def run_soak(
    *,
    cycles: int,
    interval_sec: float,
    retain_keep: int,
    duration_hours: float | None = None,
) -> dict:
    cycles = max(1, int(cycles))
    planned_duration_ms = None
    if duration_hours is not None and float(duration_hours) > 0:
        planned_duration_ms = int(float(duration_hours) * 3600 * 1000)
        # Derive cycle count from wall clock so --duration-hours 48 is honest.
        # At least 1 cycle; interval includes scan time approximately.
        interval_ms = max(1, int(float(interval_sec) * 1000))
        cycles = max(1, planned_duration_ms // interval_ms)

    results = []
    hard_fails = 0
    t_start = int(time.time() * 1000)
    deadline = (
        (t_start + planned_duration_ms) if planned_duration_ms is not None else None
    )
    i = 0
    while i < cycles:
        if deadline is not None and i > 0 and int(time.time() * 1000) >= deadline:
            break
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
        i += 1
        print(
            json.dumps(
                {
                    "cycle": i,
                    "hard_fail": row.get("hard_fail"),
                    "ok": row.get("ok"),
                    "mtf_ensure_ok": row.get("mtf_ensure_ok"),
                    "elapsed_ms": row.get("elapsed_ms"),
                    "meme_cex_fill": (row.get("short_window_ensure") or {}).get(
                        "meme_cex_fill_ratio"
                    ),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        if i >= cycles:
            break
        if deadline is not None:
            remaining = deadline - int(time.time() * 1000)
            if remaining <= 0:
                break
            sleep_s = min(float(interval_sec), max(0.0, remaining / 1000.0))
            if sleep_s > 0:
                time.sleep(sleep_s)
        elif interval_sec > 0:
            time.sleep(float(interval_sec))

    t_end = int(time.time() * 1000)
    duration_ms = t_end - t_start
    approx_48h = bool(
        planned_duration_ms is not None
        and planned_duration_ms >= int(47.5 * 3600 * 1000)
        and duration_ms >= int(47.5 * 3600 * 1000)
        and hard_fails == 0
    )
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    pack = {
        "product": "DUP Market Radar",
        "pack_kind": "soak_lab_48h" if approx_48h else "soak_lab",
        "generated_at_utc": ts,
        "disclaimer": (
            "Evidence only — not financial advice; author accepts no liability. "
            "Not a 48h soak claim unless duration≈48h and hard_fails=0."
        ),
        "cycles_planned": cycles,
        "cycles_ran": len(results),
        "interval_sec": interval_sec,
        "duration_hours_requested": duration_hours,
        "duration_ms": duration_ms,
        "hard_fails": hard_fails,
        "approx_48h_claim_ok": approx_48h,
        "results": results,
    }
    ev_dir = ROOT / "data" / "evidence"
    ev_dir.mkdir(parents=True, exist_ok=True)
    prefix = "soak_48h" if approx_48h else "soak_lab"
    path = ev_dir / f"{prefix}_{ts}.json"
    path.write_text(json.dumps(pack, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    latest = ev_dir / ("LATEST_SOAK_48H.json" if approx_48h else "LATEST_SOAK.json")
    latest.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    # Always refresh LATEST_SOAK for short or long
    (ev_dir / "LATEST_SOAK.json").write_text(
        path.read_text(encoding="utf-8"), encoding="utf-8"
    )

    # Retention
    sys.path.insert(0, str(ROOT / "scripts"))
    from evidence_retention import retain  # type: ignore

    retention = retain(keep=retain_keep)
    pack["evidence_pack"] = str(path)
    pack["retention"] = retention
    print(
        json.dumps(
            {
                "summary": {
                    "hard_fails": hard_fails,
                    "cycles_ran": len(results),
                    "duration_ms": duration_ms,
                    "approx_48h_claim_ok": approx_48h,
                    "pack": str(path),
                }
            },
            indent=2,
        )
    )
    return pack


def main() -> int:
    p = argparse.ArgumentParser(description="DUP Market Radar soak lab")
    p.add_argument("--cycles", type=int, default=3, help="number of force scans (default 3)")
    p.add_argument("--interval-sec", type=float, default=45.0, help="sleep between cycles")
    p.add_argument("--retain-keep", type=int, default=20, help="evidence packs to keep")
    p.add_argument(
        "--duration-hours",
        type=float,
        default=None,
        help="wall-clock soak duration (e.g. 48). Overrides --cycles count.",
    )
    args = p.parse_args()
    pack = run_soak(
        cycles=args.cycles,
        interval_sec=args.interval_sec,
        retain_keep=args.retain_keep,
        duration_hours=args.duration_hours,
    )
    return 0 if int(pack.get("hard_fails") or 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

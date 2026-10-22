#!/usr/bin/env python3
"""Warm smoke: force cold + warm cycles (stale-OK + MTF cache).

Usage:
  python scripts/radar_warm_smoke_lab.py
  set RADAR_PROFILE=light && python scripts/radar_warm_smoke_lab.py
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from radar.config import load_config  # noqa: E402
from radar.ingest.mtf_cache import mtf_cache  # noqa: E402
from radar.ingest.sector_poll import poll_state  # noqa: E402
from radar.native_bridge import native_status  # noqa: E402
from radar.pipeline import run_scan  # noqa: E402


def summarize(snap: dict, label: str) -> dict:
    hs = snap.get("http_stats") or {}
    swe = snap.get("short_window_ensure") or {}
    return {
        "label": label,
        "ok": bool(snap.get("ok")),
        "score_backend": snap.get("score_backend"),
        "elapsed_ms": snap.get("elapsed_ms"),
        "ingest_ms": snap.get("ingest_ms"),
        "ensure_ms": snap.get("ensure_ms"),
        "score_ms": snap.get("score_ms"),
        "venue_ms": snap.get("venue_ms") or {},
        "http_stats": {
            "gets": hs.get("gets"),
            "cache_hits": hs.get("cache_hits"),
            "stale_ok_hits": hs.get("stale_ok_hits"),
            "stale_error_fallback": hs.get("stale_error_fallback"),
            "kline_gets": hs.get("kline_gets"),
        },
        "mtf_cache_hydrate_spot": swe.get("mtf_cache_hydrate_spot"),
        "mtf_cache_hydrate_fut": swe.get("mtf_cache_hydrate_fut"),
        "mtf_cache_remember_spot": swe.get("mtf_cache_remember_spot"),
        "display_ensure_ms": swe.get("display_ensure_ms"),
        "display_ensure_wall_reason": swe.get("display_ensure_wall_reason"),
        "display_skipped_coverage": swe.get("display_skipped_coverage"),
        "mtf_ensure_ok": snap.get("mtf_ensure_ok"),
        "errors": (snap.get("errors") or [])[:6],
    }


def main() -> int:
    cfg = load_config()
    cfg = dict(cfg)
    # Prefer native when wheel present; soft if missing.
    st = native_status()
    if not st.get("available"):
        cfg["native"] = {
            **cfg.get("native", {}),
            "prefer": False,
            "fail_closed": False,
        }

    mtf_cache().reset()
    poll_state().reset()
    print("=== cycle1 force cold ===", flush=True)
    s1 = run_scan(cfg, force=True)
    c1 = summarize(s1, "force_cold")
    print(json.dumps(c1, indent=2), flush=True)

    print("=== sleep 26s for TTL expiry ===", flush=True)
    time.sleep(26)

    poll_state().reset()
    print("=== cycle2 warm ===", flush=True)
    s2 = run_scan(cfg, force=False)
    c2 = summarize(s2, "warm_1")
    print(json.dumps(c2, indent=2), flush=True)

    print("=== sleep 26s ===", flush=True)
    time.sleep(26)
    poll_state().reset()
    print("=== cycle3 warm ===", flush=True)
    s3 = run_scan(cfg, force=False)
    c3 = summarize(s3, "warm_2")
    print(json.dumps(c3, indent=2), flush=True)

    # Prefer warm with MTF hydrate or stale_ok.
    def score(c: dict) -> tuple:
        hs = c.get("http_stats") or {}
        return (
            int(c.get("mtf_cache_hydrate_spot") or 0)
            + int(c.get("mtf_cache_hydrate_fut") or 0),
            int(hs.get("stale_ok_hits") or 0),
            -int(c.get("elapsed_ms") or 10**9),
        )

    best_sum = max([c2, c3], key=score)
    best = s2 if best_sum is c2 else s3

    ev_dir = ROOT / "data" / "evidence"
    ev_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    pack = {
        "product": "DUP Market Radar",
        "pack_kind": "smoke_live_warm",
        "generated_at_utc": ts,
        "pin": "0.2.87",
        "native": st,
        "radar_profile": cfg.get("_radar_profile"),
        "disclaimer": (
            "Evidence only — not financial advice; author accepts no liability."
        ),
        "cycles": [c1, c2, c3],
        "summary": best_sum,
        "mtf_ensure_ok": best.get("mtf_ensure_ok"),
        "ok": best.get("ok"),
        "short_window_ensure": best.get("short_window_ensure"),
        "venue_ms": best.get("venue_ms"),
        "http_stats": best.get("http_stats"),
        "score_backend": best.get("score_backend"),
    }
    path = ev_dir / f"smoke_live_{ts}.json"
    text = json.dumps(pack, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8")
    (ev_dir / "LATEST.json").write_text(text, encoding="utf-8")
    print(f"evidence_pack={path}", flush=True)
    print(
        json.dumps(
            {
                "ok": best.get("ok"),
                "score_backend": best.get("score_backend"),
                "elapsed_ms": best_sum.get("elapsed_ms"),
                "ensure_ms": best_sum.get("ensure_ms"),
                "mtf_hydrate": int(best_sum.get("mtf_cache_hydrate_spot") or 0)
                + int(best_sum.get("mtf_cache_hydrate_fut") or 0),
                "stale_ok": (best_sum.get("http_stats") or {}).get("stale_ok_hits"),
            },
            indent=2,
        ),
        flush=True,
    )
    return 0 if best.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Warm smoke for 0.2.86: force cold + 2 warm cycles after TTL expiry."""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from radar.config import load_config  # noqa: E402
from radar.ingest.sector_poll import poll_state  # noqa: E402
from radar.pipeline import run_scan  # noqa: E402


def summarize(snap: dict, label: str) -> dict:
    hs = snap.get("http_stats") or {}
    swe = snap.get("short_window_ensure") or {}
    return {
        "label": label,
        "ok": bool(snap.get("ok")),
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
        "display_ensure_ms": swe.get("display_ensure_ms"),
        "display_ensure_wall_ms": swe.get("display_ensure_wall_ms"),
        "display_ensure_wall_reason": swe.get("display_ensure_wall_reason"),
        "display_skipped_coverage": swe.get("display_skipped_coverage"),
        "display_board_spot": swe.get("display_board_spot"),
        "display_board_fut": swe.get("display_board_fut"),
        "mtf_ensure_ok": snap.get("mtf_ensure_ok"),
        "errors": (snap.get("errors") or [])[:6],
    }


def main() -> int:
    cfg = load_config()
    cfg = dict(cfg)
    # Lab smoke: soft native (same honesty as radar_smoke_lab offline path).
    cfg["native"] = {
        **cfg.get("native", {}),
        "prefer": False,
        "fail_closed": False,
    }

    poll_state().reset()
    print("=== cycle1 force cold ===", flush=True)
    s1 = run_scan(cfg, force=True)
    c1 = summarize(s1, "force_cold")
    print(json.dumps(c1, indent=2), flush=True)

    print("=== sleep 26s for TTL expiry ===", flush=True)
    time.sleep(26)

    # Reset poll so CEX is due again, but force=False → KuCoin stale-OK path.
    poll_state().reset()
    print("=== cycle2 warm (due+stale_ok) ===", flush=True)
    s2 = run_scan(cfg, force=False)
    c2 = summarize(s2, "warm_1")
    print(json.dumps(c2, indent=2), flush=True)

    print("=== sleep 26s ===", flush=True)
    time.sleep(26)
    poll_state().reset()
    print("=== cycle3 warm (due+stale_ok) ===", flush=True)
    s3 = run_scan(cfg, force=False)
    c3 = summarize(s3, "warm_2")
    print(json.dumps(c3, indent=2), flush=True)

    h2 = int((s2.get("http_stats") or {}).get("stale_ok_hits") or 0)
    h3 = int((s3.get("http_stats") or {}).get("stale_ok_hits") or 0)
    best, best_sum = (s2, c2) if h2 >= h3 else (s3, c3)

    ev_dir = ROOT / "data" / "evidence"
    ev_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    pack = {
        "product": "DUP Market Radar",
        "pack_kind": "smoke_live_warm",
        "generated_at_utc": ts,
        "pin": "0.2.86",
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
    }
    path = ev_dir / f"smoke_live_{ts}.json"
    text = json.dumps(pack, indent=2, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8")
    (ev_dir / "LATEST.json").write_text(text, encoding="utf-8")
    print(f"evidence_pack={path}", flush=True)
    print(
        json.dumps(
            {
                "warm_stale_ok": best_sum["http_stats"].get("stale_ok_hits"),
                "ok": best.get("ok"),
                "elapsed_ms": best_sum.get("elapsed_ms"),
            },
            indent=2,
        ),
        flush=True,
    )
    return 0 if best.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Offline + optional live smoke lab for DUP Market Radar.

Usage:
  python scripts/radar_smoke_lab.py           # offline gates
  python scripts/radar_smoke_lab.py --live   # one run_scan (needs network)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def offline() -> int:
    from radar.config import load_config
    from radar.ingest.normalize import enrich_list
    from radar.money import PRICE_SCALE, quote_i
    from radar.native_bridge import native_status
    from radar.score.engine import score_sectors

    cfg = load_config()
    assert cfg["venues"][0] == "kucoin"
    assert cfg["native"].get("fail_closed") is True
    assert quote_i("10") == 10_000_000

    universe = {
        "spot": enrich_list(
            [
                {
                    "venue": "kucoin",
                    "market": "spot",
                    "symbol": "SOLUSDT",
                    "price": "150",
                    "change_pct": "5",
                    "quote_volume": "50000000",
                    "bid": "149.9",
                    "ask": "150.1",
                }
            ]
        ),
        "futures": [],
        "memes": [],
        "stables": [],
        "listings": [],
        "whales": [],
    }
    # prefer may require native — use soft path for lab if missing
    lab_cfg = dict(cfg)
    lab_cfg["native"] = {**cfg.get("native", {}), "prefer": False, "fail_closed": False}
    sectors, backend = score_sectors(universe, lab_cfg, int(cfg.get("top_n") or 10))
    assert backend == "python"
    assert any(r["symbol"] == "SOLUSDT" for r in sectors["spot_alts"])
    sol = next(r for r in sectors["spot_alts"] if r["symbol"] == "SOLUSDT")
    assert isinstance(sol["score_centi"], int)
    assert sol["price_i"] == 150 * PRICE_SCALE

    st = native_status()

    # Telegram offline gates (no Bot API)
    from radar.telegram.alerts import evaluate_watch
    from radar.telegram.nearest import nearest_for_action
    from radar.telegram.watches import WatchBook

    near = nearest_for_action(action="sell", price_i=sol["price_i"], setup="fade-watch")
    assert near["ok"] and near["basis"] == "analysis_zone"
    book = WatchBook(ROOT / "data" / "_smoke_tg_watches.json")
    book.ensure(1, "SOLUSDT", market="spot", entry_price_i=sol["price_i"])
    assert book.ensure(1, "SOLUSDT", market="spot") is False
    fade_snap = {
        "generated_at_ms": 10**12,
        "sectors": {
            "spot_alts": [
                {
                    "symbol": "SOLUSDT",
                    "setup": "fade-watch",
                    "price_i": sol["price_i"],
                    "score_centi": sol["score_centi"],
                    "risk_flags": [],
                }
            ]
        },
    }
    entry_high = sol["price_i"] + sol["price_i"] // 5  # ~+20% integer
    alert = evaluate_watch(
        {
            "chat_id": 1,
            "symbol": "SOLUSDT",
            "market": "spot",
            "entry_price_i": entry_high,
            "last_alert_at_ms": 0,
            "last_alert_key": "",
        },
        fade_snap,
        stop_loss_bps=100,
    )
    assert alert is not None and alert["action_suggest"] == "sell"
    removed = book.remove(1, "SOLUSDT", market="spot")
    assert removed >= 1
    # cleanup smoke file
    for p in (
        ROOT / "data" / "_smoke_tg_watches.json",
        ROOT / "data" / "_smoke_tg_watches.json.sha256",
    ):
        p.unlink(missing_ok=True)

    print(
        json.dumps(
            {
                "ok": True,
                "mode": "offline",
                "score_backend": backend,
                "native": st,
                "sol_score_centi": sol["score_centi"],
                "telegram_nearest_ok": True,
                "telegram_alert_ok": True,
            },
            indent=2,
        )
    )
    return 0


def _mtf_coverage(rows: list, keys: tuple[str, ...]) -> dict[str, int]:
    out = {k: 0 for k in keys}
    for r in rows or []:
        m = (r.get("metrics") or {}) if isinstance(r, dict) else {}
        for k in keys:
            if m.get(k) is not None:
                out[k] += 1
    return out


def live(*, write_evidence: bool = False) -> int:
    from radar.config import load_config
    from radar.pipeline import run_scan

    cfg = load_config()
    # Live lab: soft native so missing wheel does not abort evidence
    cfg = dict(cfg)
    cfg["native"] = {**cfg.get("native", {}), "fail_closed": False}
    from radar.ingest.sector_poll import poll_state

    poll_state().reset()
    snap = run_scan(cfg, force=True)
    sectors = snap.get("sectors") or {}
    summary = {
        "ok": bool(snap.get("ok")),
        "mode": "live",
        "schema_version": snap.get("schema_version"),
        "score_backend": snap.get("score_backend"),
        "universe_size": snap.get("universe_size"),
        "elapsed_ms": snap.get("elapsed_ms"),
        "ingest_ms": snap.get("ingest_ms"),
        "ensure_ms": snap.get("ensure_ms"),
        "score_ms": snap.get("score_ms"),
        "venue_ms": snap.get("venue_ms") or {},
        "errors": (snap.get("errors") or [])[:8],
        "counts": snap.get("counts"),
        "poll": snap.get("poll"),
        "due_groups": snap.get("due_groups"),
        "poll_skipped": snap.get("poll_skipped"),
        "circuit_skipped": snap.get("circuit_skipped"),
        "http_stats": snap.get("http_stats"),
        "kline_gets": ((snap.get("http_stats") or {}) or {}).get("kline_gets"),
        "dedupe": snap.get("dedupe"),
        "spot_mtf": _mtf_coverage(
            sectors.get("spot_alts") or [],
            ("change_bps_15m", "change_bps_1h", "change_bps_4h", "vol_vs_median_bps"),
        ),
        "fut_mtf": _mtf_coverage(
            sectors.get("futures") or [],
            ("change_bps_15m", "change_bps_1h", "change_bps_4h"),
        ),
        "meme_mtf": _mtf_coverage(
            sectors.get("memes") or [],
            ("change_bps", "change_bps_1h", "change_bps_5m"),
        ),
        "listings_mtf": _mtf_coverage(
            sectors.get("listings") or [],
            ("change_bps", "change_bps_15m", "change_bps_1h", "age_hours"),
        ),
        "listings_flags": {
            "cross_venue_live": sum(
                1
                for r in (sectors.get("listings") or [])
                if "cross_venue_live" in (r.get("risk_flags") or [])
            ),
            "no_live_volume": sum(
                1
                for r in (sectors.get("listings") or [])
                if "no_live_volume" in (r.get("risk_flags") or [])
            ),
            "n": len(sectors.get("listings") or []),
        },
        "short_window_ensure": snap.get("short_window_ensure"),
        "elapsed_budget_ms": 25_000,
        "elapsed_ok": int(snap.get("elapsed_ms") or 0) <= 25_000,
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if write_evidence:
        from datetime import datetime, timezone

        ev_dir = ROOT / "data" / "evidence"
        ev_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        pack = {
            "product": "DUP Market Radar",
            "pack_kind": "smoke_live",
            "generated_at_utc": ts,
            "disclaimer": "Evidence only — not financial advice; author accepts no liability.",
            "summary": summary,
            "mtf_ensure_ok": snap.get("mtf_ensure_ok"),
            "ok": snap.get("ok"),
        }
        path = ev_dir / f"smoke_live_{ts}.json"
        path.write_text(json.dumps(pack, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        latest = ev_dir / "LATEST.json"
        latest.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"evidence_pack={path}")
        try:
            sys.path.insert(0, str(ROOT / "scripts"))
            from evidence_retention import retain  # type: ignore
            print(json.dumps({"retention": retain(keep=20)}, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001
            print(f"evidence_retention_soft_fail={exc}")
    # Soft speed gate: still return 0 on ok snapshot; elapsed_ok is evidence only.
    return 0 if snap.get("ok") else 1


def main() -> int:
    p = argparse.ArgumentParser(description="DUP Market Radar smoke lab")
    p.add_argument("--live", action="store_true", help="run one live ingest+score scan")
    p.add_argument(
        "--evidence",
        action="store_true",
        help="with --live: write JSON pack under data/evidence/",
    )
    args = p.parse_args()
    if args.live:
        return live(write_evidence=bool(args.evidence))
    return offline()


if __name__ == "__main__":
    raise SystemExit(main())

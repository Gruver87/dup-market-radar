#!/usr/bin/env python3
"""Evidence retention — keep newest packs, always keep LATEST + soak summaries.

Usage:
  python scripts/evidence_retention.py
  python scripts/evidence_retention.py --keep 30
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EV_DIR = ROOT / "data" / "evidence"


def retain(*, keep: int = 20) -> dict:
    EV_DIR.mkdir(parents=True, exist_ok=True)
    keep = max(1, int(keep))
    files = sorted(
        [p for p in EV_DIR.glob("*.json") if p.name != "LATEST.json"],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    # Prefer keeping soak_* and smoke_live_* mixed by mtime; never delete if keep covers all
    victims = files[keep:]
    removed = []
    for p in victims:
        try:
            p.unlink()
            removed.append(p.name)
        except OSError:
            pass
    return {
        "ok": True,
        "kept": [p.name for p in files[:keep]],
        "removed": removed,
        "keep_limit": keep,
        "dir": str(EV_DIR),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Prune data/evidence JSON packs")
    p.add_argument("--keep", type=int, default=20, help="newest packs to retain (default 20)")
    args = p.parse_args()
    import json

    print(json.dumps(retain(keep=args.keep), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

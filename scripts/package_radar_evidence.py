#!/usr/bin/env python3
"""Package Radar evidence into a hashed run directory (honesty: missing ≠ invent PASS).

Usage:
  python scripts/package_radar_evidence.py --from-latest
  python scripts/package_radar_evidence.py --inputs data/evidence/smoke_live_….json …
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "data" / "evidence"
PACKS = ROOT / "docs" / "evidence" / "runs"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _git_head() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(ROOT),
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip()[:40]
    except Exception:  # noqa: BLE001
        return "unknown"


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def package(inputs: List[Path], *, label: str) -> Path:
    PACKS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"{stamp}-{label}"[:80]
    dest = PACKS / run_id
    dest.mkdir(parents=True, exist_ok=False)

    files_meta: List[Dict[str, Any]] = []
    statuses: List[str] = []
    for src in inputs:
        entry: Dict[str, Any] = {"source": str(src.as_posix())}
        if not src.is_file():
            entry["status"] = "missing"
            statuses.append("missing")
            files_meta.append(entry)
            continue
        name = src.name
        target = dest / name
        shutil.copy2(src, target)
        digest = _sha256_file(target)
        entry.update({"name": name, "sha256": digest, "status": "present", "bytes": target.stat().st_size})
        body = _load_json(target)
        if isinstance(body, dict):
            entry["ok"] = body.get("ok")
            entry["hard_fails"] = body.get("hard_fails")
            entry["approx_48h_claim_ok"] = body.get("approx_48h_claim_ok")
            entry["pack_kind"] = body.get("pack_kind")
            hard = body.get("hard_fails")
            hard_zero = hard is not None and int(hard) == 0
            if body.get("approx_48h_claim_ok") is True and hard_zero:
                statuses.append("pass_48h_candidate")
            elif body.get("ok") is True or hard_zero:
                statuses.append("ok")
            else:
                statuses.append("fail")
        else:
            statuses.append("present")
        files_meta.append(entry)

    # Never invent PASS — only claim when a 48h candidate is present and nothing missing/fail.
    if "missing" in statuses or "fail" in statuses:
        overall = "incomplete"
    elif "pass_48h_candidate" in statuses:
        overall = "pass_48h"
    elif statuses and all(s in {"ok", "present", "pass_48h_candidate"} for s in statuses):
        overall = "ok"
    else:
        overall = "incomplete"

    manifest = {
        "product": "DUP Market Radar",
        "run_id": run_id,
        "created_at_utc": stamp,
        "git_head": _git_head(),
        "status": overall,
        "files": files_meta,
        "honesty": (
            "status=pass_48h only when a pack sets approx_48h_claim_ok=true "
            "and hard_fails=0; missing inputs never invent PASS"
        ),
    }
    man_path = dest / "manifest.json"
    man_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (dest / "MANIFEST.sha256").write_text(
        f"{_sha256_file(man_path)}  manifest.json\n", encoding="utf-8"
    )
    readme = dest / "README.md"
    readme.write_text(
        f"# Evidence run `{run_id}`\n\n"
        f"- **status:** `{overall}`\n"
        f"- **git:** `{manifest['git_head']}`\n"
        f"- See `manifest.json` for per-file sha256.\n"
        f"- Do not claim 48h soak unless status is `pass_48h`.\n",
        encoding="utf-8",
    )
    return dest


def _latest_inputs() -> List[Path]:
    out: List[Path] = []
    for name in ("LATEST.json", "LATEST_SOAK.json"):
        p = EVIDENCE / name
        if p.is_file():
            out.append(p)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from-latest", action="store_true", help="Use data/evidence/LATEST*.json")
    ap.add_argument("--inputs", nargs="*", type=Path, default=[])
    ap.add_argument("--label", default="radar", help="Run id label suffix")
    args = ap.parse_args()
    inputs = list(args.inputs or [])
    if args.from_latest:
        inputs.extend(_latest_inputs())
    if not inputs:
        print("no inputs — pass --from-latest or --inputs PATH…", file=sys.stderr)
        print(
            json.dumps(
                {"ok": False, "status": "missing", "note": "no_inputs"},
                indent=2,
            )
        )
        return 1
    dest = package(inputs, label=str(args.label))
    man = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    print(json.dumps({"ok": True, "path": str(dest), "status": man.get("status")}, indent=2))
    return 0 if man.get("status") != "incomplete" or any(
        f.get("status") == "present" for f in man.get("files") or []
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())

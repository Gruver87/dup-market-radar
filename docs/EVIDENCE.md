# Evidence packs

Live smoke evidence is **optional** and operator-produced:

```powershell
$env:PYTHONPATH="src"
python scripts/radar_smoke_lab.py --live --evidence
```

Writes `data/evidence/smoke_live_<UTC>.json` and `data/evidence/LATEST.json` (gitignored).

Packs are **not** financial advice and do **not** imply soak / mainnet readiness. See [`DISCLAIMER.md`](DISCLAIMER.md).

Do not claim 48h soak unless a dedicated soak pack exists on disk with `hard_fails=0`.
## Local verification note (operator)

A live pack was produced on 2026-10-21 (smoke_live_*Z.json, ok=true, elapsed_ok, MTF fill 1.0 spot/fut). JSON stays gitignored under data/evidence/ — re-run --live --evidence on your host for a fresh pack.

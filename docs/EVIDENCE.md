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

## Soak lab (short gate — not automatic 48h)

```powershell
$env:PYTHONPATH="src"
python scripts/radar_soak_lab.py --cycles 3 --interval-sec 45
```

Writes `data/evidence/soak_lab_<UTC>.json` and `LATEST_SOAK.json`. Exit code 1 if `hard_fails > 0`.

### 48h wall-clock soak (operator host)

```powershell
.\scripts\radar_soak_48h.ps1
# or:
python scripts/radar_soak_lab.py --duration-hours 48 --interval-sec 300
```

Pack sets `approx_48h_claim_ok=true` and `pack_kind=soak_lab_48h` only when duration≈48h **and** `hard_fails=0`. Until then: **do not claim 48h soak**.

## Retention

```powershell
python scripts/evidence_retention.py --keep 20
```

Also runs automatically after `--live --evidence` and at the end of soak lab.
Operator note: short soak_lab 2 cycles hard_fails=0 recorded locally (gitignored JSON).

# Operations

## Local process

```powershell
.\scripts\build_native.ps1   # optional but recommended
.\scripts\run_radar.ps1
```

Default UI: `http://127.0.0.1:8787/`.

## Profiles

| Profile | How | Native | Use |
|---------|-----|--------|-----|
| **default.yaml** | Local with wheel built | prefer + fail_closed | Closest to “honest native” |
| **python-lab** | Docker compose / CI env | prefer=0 fail_closed=0 | Labs, CI, no maturin in image |
| **live smoke** | `radar_smoke_lab.py --live` | often soft | One-shot evidence |

CI job name: `test (python-lab-only)`.

## Health

- `/api/live` — process up  
- `/api/ready` — warm + persist OK  
- `/api/health` — scan / native / circuit honesty  
- `/api/metrics` — JSON; Prometheus text at `/api/metrics/prometheus`

Dashboard status line surfaces `СБОЙ ДАННЫХ`, `mtf=OK|FAIL`, and ensure fill ratios when present.

## Data dir

`data/` holds positions, auto state, watches, oid ledger, trade events (gitignored JSON/JSONL). Back up before wiping. Corrupt `auto_positions.json` **blocks enabling** auto-trade.

## Smoke

```powershell
$env:PYTHONPATH="src"
python scripts/radar_smoke_lab.py          # offline
python scripts/radar_smoke_lab.py --live   # network
```

`elapsed_ok` vs ~25s is **evidence**, not a hard CI fail.

## Docker

```powershell
docker compose up --build
```

- Publishes `127.0.0.1:8787` by default.  
- Secrets via env / `.env` — never baked into the image.  
- Lab-python native overrides — see compose header comments.

## Verify (единый вход)

```powershell
.\scripts\verify.ps1 -Profile List
.\scripts\verify.ps1 -Profile Quick      # pytest + smoke offline
.\scripts\verify.ps1 -Profile Lab        # + live evidence (default)
.\scripts\verify.ps1 -Profile PreSoak    # + native + short soak
.\scripts\verify.ps1 -Profile Soak48h    # PreSoak then 48h
```

Wrappers (то же самое): `pre_soak_gate.ps1`, `radar_soak_48h.ps1`.

## No soak claim

Do not report “48h soak passed” unless you ran it and stored an evidence pack on disk with `approx_48h_claim_ok=true`.

## Native Docker

```powershell
docker compose -f docker-compose.native.yml up --build
```

Uses `Dockerfile.native` (maturin wheel, `RADAR_NATIVE_PREFER=1`). Lab compose remains python-only.

## Evidence

```powershell
python scripts/radar_smoke_lab.py --live --evidence
```

See [`EVIDENCE.md`](EVIDENCE.md).
